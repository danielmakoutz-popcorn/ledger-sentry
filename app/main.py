import secrets, asyncio
from datetime import date
from fastapi import FastAPI, Request, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from .config import APP_PASSWORD, SECRET_KEY
from .db import init_db, add_manual
from .logic import dashboard_data, import_csv, ask, cancellation_guide, cull_candidates
from . import plaid
from pydantic import BaseModel

app=FastAPI(title='Ledger Sentry')
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, https_only=False, same_site='lax')
app.mount('/static', StaticFiles(directory='app/static'), name='static')
templates=Jinja2Templates(directory='app/templates')

_sync_task=None

@app.on_event('startup')
async def startup():
    global _sync_task
    init_db()
    if plaid.enabled():
        _sync_task=asyncio.create_task(plaid.sync_loop())

@app.on_event('shutdown')
async def shutdown():
    global _sync_task
    if _sync_task:
        _sync_task.cancel()

def authed(request): return bool(request.session.get('auth'))

def guard(request):
    if not authed(request): return RedirectResponse('/login', status_code=303)

@app.get('/login', response_class=HTMLResponse)
def login_page(request:Request): return templates.TemplateResponse(request,'login.html',{})

@app.post('/login')
def login(request:Request, password:str=Form(...)):
    if secrets.compare_digest(password, APP_PASSWORD):
        request.session['auth']=True
        return RedirectResponse('/', status_code=303)
    return templates.TemplateResponse(request,'login.html',{'error':'Wrong password.'},status_code=401)

@app.post('/logout')
def logout(request:Request):
    request.session.clear(); return RedirectResponse('/login',status_code=303)

@app.get('/', response_class=HTMLResponse)
def home(request:Request):
    g=guard(request)
    if g: return g
    data=dashboard_data()
    return templates.TemplateResponse(request,'dashboard.html',{'d':data})

@app.get('/transactions', response_class=HTMLResponse)
def transactions(request:Request):
    g=guard(request)
    if g: return g
    from .db import connect
    with connect() as con:
        rows=con.execute('SELECT * FROM transactions ORDER BY tx_date DESC,id DESC LIMIT 500').fetchall()
    return templates.TemplateResponse(request,'transactions.html',{'rows':rows,'today':date.today().isoformat()})

@app.post('/transactions/manual')
def manual(request:Request, tx_date:str=Form(...), description:str=Form(...), amount:float=Form(...), category:str=Form(...), account:str=Form('Manual')):
    g=guard(request)
    if g:return g
    from .logic import normalize_merchant
    add_manual(tx_date,description,normalize_merchant(description),amount,category,account)
    return RedirectResponse('/transactions',status_code=303)

@app.get('/import', response_class=HTMLResponse)
def import_page(request:Request):
    g=guard(request)
    if g:return g
    return templates.TemplateResponse(request,'import.html',{})

@app.post('/import', response_class=HTMLResponse)
async def upload(request:Request, file:UploadFile=File(...), account:str=Form('Bank'), sign_mode:str=Form('negative_is_spend')):
    g=guard(request)
    if g:return g
    content=await file.read()
    imported,skipped,errors=import_csv(content,account,sign_mode)
    return templates.TemplateResponse(request,'import.html',{'result':f'Imported {imported} new transactions.','errors':errors})

@app.get('/assistant', response_class=HTMLResponse)
def assistant_page(request:Request):
    g=guard(request)
    if g:return g
    return templates.TemplateResponse(request,'assistant.html',{})

@app.post('/assistant', response_class=HTMLResponse)
def assistant_ask(request:Request, question:str=Form(...)):
    g=guard(request)
    if g:return g
    result=ask(question)
    return templates.TemplateResponse(request,'assistant.html',{'question':question,'answer':result})

@app.get('/cancel/{merchant}', response_class=HTMLResponse)
def cancel_page(request:Request,merchant:str):
    g=guard(request)
    if g:return g
    return templates.TemplateResponse(request,'cancel.html',{'guide':cancellation_guide(merchant),'merchant':merchant})


class PublicToken(BaseModel):
    public_token: str

@app.get('/banks', response_class=HTMLResponse)
def banks(request:Request):
    g=guard(request)
    if g:return g
    return templates.TemplateResponse(request,'banks.html',{'enabled':plaid.enabled(),'items':plaid.list_items(),'plaid_env':plaid.PLAID_ENV})

@app.post('/plaid/link-token')
async def plaid_link_token(request:Request):
    if not authed(request): return JSONResponse({'error':'unauthorized'},status_code=401)
    if not plaid.enabled(): return JSONResponse({'error':'Plaid is not configured'},status_code=503)
    try: return {'link_token':await plaid.create_link_token()}
    except Exception as e: return JSONResponse({'error':str(e)},status_code=502)

@app.post('/plaid/exchange')
async def plaid_exchange(request:Request, payload:PublicToken):
    if not authed(request): return JSONResponse({'error':'unauthorized'},status_code=401)
    try:
        access_token,item_id=await plaid.exchange(payload.public_token)
        plaid.save_item(item_id,access_token)
        row=[r for r in plaid.list_items() if r['item_id']==item_id][0]
        sync=await plaid.sync_item(row['id'])
        return {'ok':True,'sync':sync}
    except Exception as e: return JSONResponse({'error':str(e)},status_code=502)

@app.post('/banks/{db_id}/sync')
async def bank_sync(request:Request, db_id:int):
    g=guard(request)
    if g:return g
    try: await plaid.sync_item(db_id)
    except Exception: pass
    return RedirectResponse('/banks',status_code=303)

@app.post('/banks/{db_id}/disconnect')
async def bank_disconnect(request:Request, db_id:int):
    g=guard(request)
    if g:return g
    row=plaid.get_item(db_id)
    if row:
        try: await plaid.remove_remote(plaid.decrypt_token(row['access_token_enc']))
        except Exception: pass
        plaid.delete_item(db_id)
    return RedirectResponse('/banks',status_code=303)

@app.get('/api/summary')
def api_summary(request:Request):
    if not authed(request): return {'error':'unauthorized'}
    d=dashboard_data()
    return {'month':d['month'],'spending':d['total'],'categories':dict(d['categories']),'alerts':d['alerts'],'recurring':d['recurring']}

@app.get('/health')
def health(): return {'ok':True}
