import asyncio, base64, hashlib, os
from datetime import datetime, timezone
import httpx
from cryptography.fernet import Fernet
from .config import SECRET_KEY
from .db import connect, upsert_external_transaction
from .logic import normalize_merchant

PLAID_CLIENT_ID = os.getenv('PLAID_CLIENT_ID','').strip()
PLAID_SECRET = os.getenv('PLAID_SECRET','').strip()
PLAID_ENV = os.getenv('PLAID_ENV','sandbox').strip().lower()
PLAID_REDIRECT_URI = os.getenv('PLAID_REDIRECT_URI','').strip()
SYNC_MINUTES = max(30, int(os.getenv('LEDGER_SYNC_MINUTES','240')))

BASES = {
    'sandbox':'https://sandbox.plaid.com',
    'production':'https://production.plaid.com',
}

def enabled():
    return bool(PLAID_CLIENT_ID and PLAID_SECRET and PLAID_ENV in BASES)

def _fernet():
    digest = hashlib.sha256(SECRET_KEY.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))

def encrypt_token(token): return _fernet().encrypt(token.encode()).decode()
def decrypt_token(token): return _fernet().decrypt(token.encode()).decode()

async def _post(path, body):
    if not enabled(): raise RuntimeError('Plaid is not configured')
    headers={'PLAID-CLIENT-ID':PLAID_CLIENT_ID,'PLAID-SECRET':PLAID_SECRET,'Content-Type':'application/json'}
    async with httpx.AsyncClient(timeout=35) as client:
        r=await client.post(BASES[PLAID_ENV]+path,headers=headers,json=body)
        if r.is_error:
            try: detail=r.json()
            except Exception: detail=r.text
            raise RuntimeError(f'Plaid {path} failed: {detail}')
        return r.json()

async def create_link_token(client_user_id='ledger-household'):
    body={
        'client_name':'Ledger Sentry',
        'language':'en',
        'country_codes':['US'],
        'user':{'client_user_id':client_user_id},
        'products':['transactions'],
        'transactions':{'days_requested':730},
    }
    if PLAID_REDIRECT_URI: body['redirect_uri']=PLAID_REDIRECT_URI
    return (await _post('/link/token/create',body))['link_token']

async def exchange(public_token):
    data=await _post('/item/public_token/exchange',{'public_token':public_token})
    return data['access_token'], data['item_id']

async def remove_remote(access_token):
    return await _post('/item/remove',{'access_token':access_token})

PFC_MAP = {
    'FOOD_AND_DRINK':'Food & Dining',
    'GENERAL_MERCHANDISE':'Shopping',
    'TRANSPORTATION':'Transport & Travel',
    'TRAVEL':'Transport & Travel',
    'ENTERTAINMENT':'Entertainment',
    'RENT_AND_UTILITIES':'Utilities',
    'HOME_IMPROVEMENT':'Housing',
    'MEDICAL':'Health',
    'PERSONAL_CARE':'Health',
    'LOAN_PAYMENTS':'Housing',
    'INCOME':'Income',
    'TRANSFER_IN':'Income',
    'TRANSFER_OUT':'Other',
    'BANK_FEES':'Other',
    'GOVERNMENT_AND_NON_PROFIT':'Other',
}

def plaid_category(tx):
    pfc=tx.get('personal_finance_category') or {}
    primary=(pfc.get('primary') or '').upper()
    detailed=(pfc.get('detailed') or '').upper()
    name=((tx.get('merchant_name') or tx.get('name') or '')+' '+detailed).lower()
    if any(x in name for x in ['grocery','supermarket']): return 'Groceries'
    if any(x in name for x in ['gasoline','fuel']): return 'Gas & Auto'
    if any(x in name for x in ['subscription','streaming']): return 'Subscriptions'
    return PFC_MAP.get(primary,'Other')

def save_item(item_id, access_token):
    with connect() as con:
        con.execute("""INSERT INTO bank_items(item_id,access_token_enc,cursor,label,created_at)
            VALUES(?,?,?,?,CURRENT_TIMESTAMP)
            ON CONFLICT(item_id) DO UPDATE SET access_token_enc=excluded.access_token_enc""",
            (item_id,encrypt_token(access_token),'',f'Bank • {item_id[-6:]}'))

def list_items():
    with connect() as con:
        return con.execute('SELECT id,item_id,label,last_sync,last_error,created_at FROM bank_items ORDER BY created_at').fetchall()

def get_item(db_id):
    with connect() as con:
        return con.execute('SELECT * FROM bank_items WHERE id=?',(db_id,)).fetchone()

def delete_item(db_id):
    with connect() as con: con.execute('DELETE FROM bank_items WHERE id=?',(db_id,))

async def sync_item(db_id):
    row=get_item(db_id)
    if not row: return {'added':0,'modified':0,'removed':0}
    token=decrypt_token(row['access_token_enc'])
    cursor=row['cursor'] or None
    added_n=modified_n=removed_n=0
    try:
        while True:
            body={'access_token':token,'count':500,'options':{'include_original_description':True}}
            if cursor: body['cursor']=cursor
            data=await _post('/transactions/sync',body)
            for bucket in ('added','modified'):
                for tx in data.get(bucket,[]):
                    desc=tx.get('original_description') or tx.get('name') or tx.get('merchant_name') or 'Unknown'
                    merchant=tx.get('merchant_name') or normalize_merchant(desc)
                    d=tx.get('authorized_date') or tx.get('date')
                    if not d: continue
                    upsert_external_transaction({
                        'tx_date':d,
                        'description':desc,
                        'merchant':merchant,
                        'amount':round(float(tx.get('amount') or 0),2),
                        'category':plaid_category(tx),
                        'account':f"Plaid • {(tx.get('account_id') or '')[-6:]}",
                        'source':'plaid',
                        'import_key':'plaid|'+tx['transaction_id'],
                    })
                    if bucket=='added': added_n+=1
                    else: modified_n+=1
            for tx in data.get('removed',[]):
                tid=tx.get('transaction_id')
                if tid:
                    with connect() as con: con.execute('DELETE FROM transactions WHERE import_key=?',('plaid|'+tid,))
                    removed_n+=1
            cursor=data.get('next_cursor') or cursor
            if not data.get('has_more'): break
        with connect() as con:
            con.execute('UPDATE bank_items SET cursor=?,last_sync=?,last_error=NULL WHERE id=?',
                        (cursor,datetime.now(timezone.utc).isoformat(),db_id))
        return {'added':added_n,'modified':modified_n,'removed':removed_n}
    except Exception as e:
        with connect() as con:
            con.execute('UPDATE bank_items SET last_error=? WHERE id=?',(str(e)[:500],db_id))
        raise

async def sync_all():
    if not enabled(): return []
    results=[]
    for r in list_items():
        try: results.append((r['id'],await sync_item(r['id'])))
        except Exception as e: results.append((r['id'],{'error':str(e)}))
    return results

async def sync_loop():
    while True:
        try: await sync_all()
        except Exception: pass
        await asyncio.sleep(SYNC_MINUTES*60)
