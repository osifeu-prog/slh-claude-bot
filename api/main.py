import os
from fastapi import FastAPI
from dotenv import load_dotenv
import asyncpg
import docker

load_dotenv()

DB_URL = os.getenv("DB_URL")
docker_client = docker.from_env()

app = FastAPI(title="SLH Core API", version="1.0")

@app.get("/health")
async def health():
    try:
        conn = await asyncpg.connect(DB_URL)
        await conn.execute("SELECT 1")
        await conn.close()
        db_status = "ok"
    except Exception as e:
        db_status = f"error: {e}"
    try:
        docker_client.ping()
        docker_status = "ok"
    except:
        docker_status = "error"
    return {"api": "ok", "db": db_status, "docker": docker_status}

@app.get("/containers")
async def list_containers():
    containers = docker_client.containers.list(all=True)
    return [{"name": c.name, "status": c.status} for c in containers]

# הוסף כאן בהמשך endpoints נוספים


from fastapi import Body
import httpx

SLH_CONTRACT = '0xACb0A09414CEA1C879c67bB7A877E4e19480f022'
BSC_RPC = 'https://bsc-dataseed.binance.org/'
OWNER_ID = 224223270
BOT_TOKEN = os.getenv('ADMIN_BOT_TOKEN', '')

@app.post('/api/connect/wallet')
async def connect_wallet(data: dict = Body(...)):
    wallet = data.get('wallet_address','').lower()
    tg_id = data.get('telegram_id')
    tg_user = data.get('telegram_username','')
    padded = wallet.replace('0x','').zfill(64)
    payload = {'jsonrpc':'2.0','method':'eth_call','params':[{'to':SLH_CONTRACT,'data':'0x70a08231'+padded},'latest'],'id':1}
    async with httpx.AsyncClient() as c:
        r = await c.post(BSC_RPC, json=payload, timeout=10)
        raw = int(r.json().get('result','0x0'),16)
        balance = raw / (10**15)
    tier = 'whale' if balance>=1_000_000 else 'major' if balance>=100_000 else 'holder' if balance>=10_000 else 'investor' if balance>=1_000 else 'member'
    conn = await asyncpg.connect(DB_URL)
    await conn.execute('''CREATE TABLE IF NOT EXISTS connected_wallets (wallet_address TEXT PRIMARY KEY, telegram_id BIGINT, telegram_username TEXT, slh_balance NUMERIC, tier TEXT, connected_at TIMESTAMP DEFAULT NOW())''')
    await conn.execute('INSERT INTO connected_wallets (wallet_address,telegram_id,telegram_username,slh_balance,tier) VALUES(\,\,\,\,\) ON CONFLICT (wallet_address) DO UPDATE SET slh_balance=\,tier=\,telegram_id=COALESCE(\,connected_wallets.telegram_id)', wallet,tg_id,tg_user,balance,tier)
    await conn.close()
    if BOT_TOKEN and tg_id:
        async with httpx.AsyncClient() as c:
            await c.post(f'https://api.telegram.org/bot{BOT_TOKEN}/sendMessage',json={'chat_id':tg_id,'text':f'✅ ארנק חובר!\\n{wallet[:6]}...{wallet[-4:]}\\n{balance:,.0f} SLH','parse_mode':'HTML'})
    return {'status':'connected','wallet':wallet,'slh_balance':balance,'tier':tier}
