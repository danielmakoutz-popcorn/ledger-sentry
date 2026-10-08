import csv, hashlib, io, re, statistics
from collections import defaultdict
from datetime import date, datetime
from urllib.parse import quote_plus
from .db import connect
from .config import ALERT_PERCENT, ALERT_DOLLARS

CATEGORY_RULES = {
    'Food & Dining': ['restaurant','grill','pizza','burger','taco','coffee','cafe','bar ','doordash','uber eats','grubhub','mcdonald','wendy','subway','chipotle','starbucks','baja fresh'],
    'Groceries': ['walmart','safeway','aldi','trader joe','whole foods','grocery','market','costco','sam\'s club'],
    'Gas & Auto': ['shell','chevron','exxon','mobil','76 ','arco','gas','fuel','autozone','o\'reilly','car wash'],
    'Transport & Travel': ['uber','lyft','frontier','southwest','delta','united airlines','american airlines','hotel','airbnb','booking.com','expedia'],
    'Entertainment': ['steam','playstation','xbox','nintendo','cinema','theatre','theater','ticketmaster'],
    'Subscriptions': ['netflix','spotify','paramount','hulu','disney','peacock','youtube','adobe','apple.com/bill','max.com','hbo'],
    'Utilities': ['electric','energy','nv energy','water','sewer','internet','cox','verizon','at&t','tmobile','t-mobile','phone'],
    'Housing': ['mortgage','rent','hoa'],
    'Insurance': ['insurance','geico','progressive','state farm','allstate'],
    'Shopping': ['amazon','target','best buy','home depot','lowes','etsy','ebay','walgreens','cvs'],
    'Health': ['pharmacy','doctor','dental','hospital','clinic'],
    'Income': ['payroll','direct deposit','salary','deposit'],
}

KNOWN_GUIDES = {
    'netflix': ('Netflix', ['Open Manage Membership.', 'Choose Cancel.', 'Finish the cancellation confirmation.'], 'https://help.netflix.com/en/node/407'),
    'spotify': ('Spotify Premium', ['Open your Spotify account page.', 'Open Manage your plan.', 'Choose Cancel subscription.'], 'https://support.spotify.com/us/article/cancel-premium/'),
    'youtube': ('YouTube Premium', ['Open YouTube paid memberships.', 'Choose Manage membership, then Deactivate.', 'Continue to cancel and confirm.'], 'https://support.google.com/youtube/answer/6308278'),
    'adobe': ('Adobe', ['Sign in to your Adobe account.', 'Choose Manage plan for the plan.', 'Choose Cancel your plan and follow the prompts.'], 'https://helpx.adobe.com/account/individual/subscriptions-and-plans/renewals-and-cancellations/cancel-adobe-subscription.html'),
    'apple': ('Apple subscription', ['Open Settings on an Apple device.', 'Tap your name, then Subscriptions.', 'Choose the subscription and tap Cancel Subscription.'], 'https://support.apple.com/118428'),
    'xbox': ('Microsoft / Xbox', ['Open your Microsoft Services & subscriptions page.', 'Find the subscription and choose Manage.', 'Choose Cancel subscription or Turn off recurring billing.'], 'https://support.microsoft.com/account-billing/cancel-a-microsoft-subscription-cca7a646-7b14-8b06-5d1a-6d5b1d5d16db'),
    'paramount': ('Paramount+', ['Sign in to the service or billing provider you subscribed through.', 'Open Account or Subscription & Billing.', 'Choose Cancel Subscription and confirm.'], 'https://help.paramountplus.com/s/article/PD-How-do-I-cancel-my-subscription'),
    'hulu': ('Hulu', ['Sign in to your Hulu account.', 'Open Account.', 'Under Your Subscription, choose Cancel and complete the prompts.'], 'https://help.hulu.com/article/hulu-cancel-hulu-subscription'),
    'disney': ('Disney+', ['Sign in to Disney+.', 'Open Account and select your subscription.', 'Choose Cancel Subscription and confirm.'], 'https://help.disneyplus.com/article/disneyplus-cancel'),
    'peacock': ('Peacock', ['Sign in to your Peacock account.', 'Open Plans & Payment.', 'Choose Change or Cancel Plan and follow the prompts.'], 'https://www.peacocktv.com/help/article/cancellation'),
    'max': ('Max', ['Sign in to Max and open Subscription.', 'Identify who bills you.', 'Cancel through Max or the listed billing provider.'], 'https://help.max.com/us/Answer/Detail/000002526'),
}

def normalize_merchant(text):
    s = (text or '').strip().lower()
    s = re.sub(r'\b(pos|debit|purchase|card|visa|mc|ach|payment)\b', ' ', s)
    s = re.sub(r'[#*]\w+', ' ', s)
    s = re.sub(r'\b\d{4,}\b', ' ', s)
    s = re.sub(r'\s+', ' ', s).strip(' -_')
    words = s.split()
    return ' '.join(words[:6]).title() or 'Unknown'

def categorize(description, amount):
    text = description.lower()
    if amount < 0 and any(x in text for x in CATEGORY_RULES['Income']):
        return 'Income'
    for category, terms in CATEGORY_RULES.items():
        if category == 'Income':
            continue
        if any(term in text for term in terms):
            return category
    return 'Other'

def parse_date(value):
    value = value.strip()
    fmts = ['%Y-%m-%d','%m/%d/%Y','%m/%d/%y','%m-%d-%Y','%b %d, %Y','%B %d, %Y']
    for fmt in fmts:
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            pass
    raise ValueError(f'Unrecognized date: {value}')

def pick(row, names):
    low = {k.lower().strip(): v for k,v in row.items() if k}
    for n in names:
        if n in low and str(low[n]).strip():
            return str(low[n]).strip()
    return ''

def parse_amount(row):
    raw = pick(row, ['amount','transaction amount','amt'])
    if raw:
        return float(raw.replace('$','').replace(',','').replace('(','-').replace(')',''))
    debit = pick(row, ['debit','withdrawal','charge'])
    credit = pick(row, ['credit','deposit'])
    if debit:
        return float(debit.replace('$','').replace(',',''))
    if credit:
        return -float(credit.replace('$','').replace(',',''))
    raise ValueError('No amount/debit/credit column found')

def import_csv(content: bytes, account: str, sign_mode: str):
    text = content.decode('utf-8-sig', errors='replace')
    reader = csv.DictReader(io.StringIO(text))
    imported, skipped, errors = 0, 0, []
    from .db import insert_transaction
    for i, row in enumerate(reader, start=2):
        try:
            d = pick(row, ['date','transaction date','posted date','posting date'])
            desc = pick(row, ['description','merchant','name','details','transaction description','memo'])
            if not d or not desc:
                raise ValueError('Need date and description columns')
            amount = parse_amount(row)
            if sign_mode == 'negative_is_spend':
                spend = -amount
            else:
                spend = amount
            # Internal convention: positive = money spent, negative = money received/refund.
            merchant = normalize_merchant(desc)
            category = categorize(desc, spend)
            tx_date = parse_date(d)
            key_raw = f'{account}|{tx_date}|{desc}|{spend:.2f}'
            key = hashlib.sha256(key_raw.encode()).hexdigest()
            added = insert_transaction({
                'tx_date': tx_date, 'description': desc, 'merchant': merchant,
                'amount': round(spend,2), 'category': category, 'account': account,
                'source': 'csv', 'import_key': key
            })
            imported += added
            if not added:
                skipped += 1
        except Exception as e:
            if len(errors) < 8:
                errors.append(f'Row {i}: {e}')
    return imported, skipped, errors

def month_bounds(month=None):
    today = date.today()
    if month is None:
        y,m = today.year,today.month
    else:
        y,m = map(int, month.split('-'))
    start = date(y,m,1)
    if m == 12: end = date(y+1,1,1)
    else: end = date(y,m+1,1)
    return start.isoformat(), end.isoformat()

def dashboard_data():
    start, end = month_bounds()
    with connect() as con:
        rows = con.execute('SELECT * FROM transactions WHERE tx_date>=? AND tx_date<? ORDER BY tx_date DESC, id DESC',(start,end)).fetchall()
        recent = con.execute('SELECT * FROM transactions ORDER BY tx_date DESC, id DESC LIMIT 18').fetchall()
        all_rows = con.execute('SELECT * FROM transactions ORDER BY tx_date, id').fetchall()
    spending = [r for r in rows if r['amount'] > 0]
    total = sum(r['amount'] for r in spending)
    cats = defaultdict(float)
    for r in spending: cats[r['category']] += r['amount']
    categories = sorted(cats.items(), key=lambda x:x[1], reverse=True)
    recurring = detect_recurring(all_rows)
    alerts = price_alerts(recurring)
    return {'total': total, 'categories': categories, 'recent': recent, 'recurring': recurring, 'alerts': alerts, 'month': start[:7]}

def detect_recurring(rows):
    groups = defaultdict(list)
    for r in rows:
        if r['amount'] > 0:
            groups[r['merchant']].append(r)
    out = []
    for merchant, items in groups.items():
        if len(items) < 2:
            continue
        items = sorted(items, key=lambda r:r['tx_date'])
        dates = [datetime.fromisoformat(r['tx_date']).date() for r in items]
        gaps = [(dates[i]-dates[i-1]).days for i in range(1,len(dates))]
        recent_gaps = gaps[-4:]
        cadence = None
        if any(5 <= g <= 10 for g in recent_gaps): cadence='Weekly'
        if any(20 <= g <= 40 for g in recent_gaps): cadence='Monthly'
        if any(330 <= g <= 400 for g in recent_gaps): cadence='Annual'
        if not cadence: continue
        amounts = [float(r['amount']) for r in items]
        last = amounts[-1]
        typical = statistics.median(amounts[:-1] or amounts)
        variance = abs(last-typical) / max(typical, 0.01)
        # tolerate bills that vary, but require a reasonably repeated cadence
        category = items[-1]['category']
        out.append({
            'merchant': merchant, 'amount': last, 'typical': typical,
            'cadence': cadence, 'category': category, 'last_date': items[-1]['tx_date'],
            'count': len(items), 'variance': variance,
            'annualized': last * (52 if cadence=='Weekly' else 12 if cadence=='Monthly' else 1)
        })
    return sorted(out, key=lambda x:x['annualized'], reverse=True)

def price_alerts(recurring):
    alerts=[]
    for r in recurring:
        base = r['typical']
        delta = r['amount'] - base
        pct = (delta/base*100) if base else 0
        if delta > 0 and (pct >= ALERT_PERCENT or delta >= ALERT_DOLLARS):
            alerts.append({**r, 'delta': delta, 'pct': pct})
    return sorted(alerts, key=lambda x:x['delta'], reverse=True)

def cull_candidates(recurring):
    nonessential = {'Subscriptions','Entertainment','Food & Dining','Shopping','Other'}
    cands=[]
    for r in recurring:
        if r['category'] not in nonessential:
            continue
        score = r['annualized']
        if r['category'] == 'Subscriptions': score *= 1.35
        cands.append({**r, 'score': score})
    return sorted(cands, key=lambda x:x['score'], reverse=True)

def cancellation_guide(merchant):
    low=merchant.lower()
    for key, (name, steps, url) in KNOWN_GUIDES.items():
        if key in low:
            return {'name':name, 'steps':steps, 'url':url, 'known':True}
    q = quote_plus(f'{merchant} official cancel subscription')
    return {
        'name': merchant,
        'steps': [
            'Check the merchant charge details to confirm who actually bills the subscription.',
            'Sign in to the merchant or billing provider and open Account, Billing, Plan, or Subscriptions.',
            'Look for Cancel, End membership, or Turn off recurring billing. Save the confirmation.',
            'If no cancel option appears, check whether Apple, Google Play, PayPal, your mobile carrier, or another partner is the billing provider.'
        ],
        'url': f'https://www.google.com/search?q={q}', 'known':False
    }

def category_total(category, month=None):
    start,end=month_bounds(month)
    with connect() as con:
        row=con.execute('SELECT COALESCE(SUM(amount),0) s FROM transactions WHERE category=? AND amount>0 AND tx_date>=? AND tx_date<?',(category,start,end)).fetchone()
    return float(row['s'])

def ask(question):
    q=question.strip().lower()
    with connect() as con:
        all_rows=con.execute('SELECT * FROM transactions ORDER BY tx_date,id').fetchall()
    recurring=detect_recurring(all_rows)
    if any(x in q for x in ['cull','cut','what can i cancel','subscriptions to cancel','save money']):
        c=cull_candidates(recurring)[:8]
        if not c: return {'title':'Cull candidates','text':'I do not have enough recurring discretionary charges yet. Import a few months of transactions and I’ll have sharper teeth.', 'items':[]}
        annual=sum(x['annualized'] for x in c)
        return {'title':'Best cull candidates','text':f'These recurring discretionary charges add up to about ${annual:,.0f}/year if all were removed.', 'items':[f"{x['merchant']}: ${x['amount']:.2f} {x['cadence'].lower()} (~${x['annualized']:.0f}/yr)" for x in c]}
    if q.startswith('cancel ') or 'how do i cancel' in q or 'how to cancel' in q:
        merchant=re.sub(r'^(how do i |how to )?cancel\s+','',q).strip(' ?')
        if not merchant:
            return {'title':'Cancel something','text':'Tell me the merchant name, for example: “How do I cancel Spotify?”','items':[]}
        guide=cancellation_guide(merchant)
        return {'title':f"Cancel {guide['name']}", 'text':'Cancellation path:', 'items':guide['steps'], 'url':guide['url']}
    if any(x in q for x in ['went up','raised','increased','price increase']):
        a=price_alerts(recurring)
        if not a: return {'title':'Price increases','text':'No recurring charges currently cross your alert threshold.','items':[]}
        return {'title':'Price increases','text':'These recurring charges are above their prior baseline:', 'items':[f"{x['merchant']}: ${x['typical']:.2f} → ${x['amount']:.2f} (+{x['pct']:.1f}%)" for x in a[:10]]}
    category_aliases = {
        'food':'Food & Dining','restaurant':'Food & Dining','dining':'Food & Dining',
        'groceries':'Groceries','grocery':'Groceries','gas':'Gas & Auto','auto':'Gas & Auto',
        'shopping':'Shopping','subscriptions':'Subscriptions','utilities':'Utilities','travel':'Transport & Travel','entertainment':'Entertainment'
    }
    for word,cat in category_aliases.items():
        if word in q:
            total=category_total(cat)
            return {'title':cat,'text':f'You have spent ${total:,.2f} in {cat.lower()} this month.','items':[]}
    return {'title':'Try asking me','text':'I currently understand questions about categories, price increases, cancellations, and what recurring charges could be culled.', 'items':['“What went up?”','“How much on food this month?”','“What can I cull?”','“How do I cancel Spotify?”']}
