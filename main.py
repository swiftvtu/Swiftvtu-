"""
SwiftVTU Backend — Single File Version
All-in-one FastAPI app: models, auth, routes, services.
Built for pydantic v1 (no Rust dependency) — deploys cleanly on Render free tier.
"""
import os
import uuid
import secrets
from datetime import datetime, timedelta
from typing import Optional, List
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr, validator
from passlib.context import CryptContext
from jose import JWTError, jwt
import httpx

from motor.motor_asyncio import AsyncIOMotorClient
from beanie import Document, init_beanie, Indexed

# ════════════════════════════════════════════════════════════════
# CONFIG — reads from environment variables set on Render
# ════════════════════════════════════════════════════════════════
MONGODB_URL = os.getenv("MONGODB_URL", "mongodb://localhost:27017")
MONGODB_DB_NAME = os.getenv("MONGODB_DB_NAME", "swiftvtu")
SECRET_KEY = os.getenv("SECRET_KEY", "change-this-secret-key")
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 24 hours

CLUBKONNECT_USER_ID = os.getenv("CLUBKONNECT_USER_ID", "")
CLUBKONNECT_API_KEY = os.getenv("CLUBKONNECT_API_KEY", "")
CLUBKONNECT_BASE_URL = "https://www.nellobytesystems.com"

PAYSTACK_SECRET_KEY = os.getenv("PAYSTACK_SECRET_KEY", "")
FLW_SECRET_KEY = os.getenv("FLW_SECRET_KEY", "")
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY", "")
EMAIL_FROM = os.getenv("EMAIL_FROM", "noreply@swiftvtu.com")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5500")

ALGORITHM = "HS256"
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
bearer_scheme = HTTPBearer()

# ════════════════════════════════════════════════════════════════
# DATABASE MODELS (Beanie / MongoDB)
# ════════════════════════════════════════════════════════════════
class User(Document):
    first_name: str
    last_name: str
    email: Indexed(EmailStr, unique=True)
    phone: Indexed(str, unique=True)
    hashed_password: str
    role: str = "user"          # user | admin
    status: str = "active"      # active | blocked
    balance_kobo: int = 0
    is_email_verified: bool = False
    created_at: datetime = datetime.utcnow()
    updated_at: datetime = datetime.utcnow()
    last_login: Optional[datetime] = None

    class Settings:
        name = "users"

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"

    @property
    def balance_naira(self) -> float:
        return self.balance_kobo / 100


class Transaction(Document):
    reference: Indexed(str, unique=True)
    user_id: Indexed(str)
    type: str                   # airtime | data | electricity | cable_tv | wallet_funding | admin_credit | admin_debit
    status: str = "pending"     # pending | success | failed
    amount_kobo: int
    description: str = ""
    network: Optional[str] = None
    phone: Optional[str] = None
    gateway: Optional[str] = None
    raw_response: Optional[dict] = None
    token: Optional[str] = None
    created_at: datetime = datetime.utcnow()
    completed_at: Optional[datetime] = None

    class Settings:
        name = "transactions"

    @property
    def amount_naira(self) -> float:
        return self.amount_kobo / 100


class WalletFundingLog(Document):
    user_id: str
    reference: str
    gateway: str
    amount_kobo: int
    status: str = "pending"
    created_at: datetime = datetime.utcnow()

    class Settings:
        name = "wallet_funding_logs"


# ════════════════════════════════════════════════════════════════
# PYDANTIC SCHEMAS (request/response bodies)
# ════════════════════════════════════════════════════════════════
class RegisterRequest(BaseModel):
    first_name: str
    last_name: str
    email: EmailStr
    phone: str
    password: str

    @validator("password")
    def password_length(cls, v):
        if len(v) < 6:
            raise ValueError("Password must be at least 6 characters")
        return v


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UserResponse(BaseModel):
    id: str
    first_name: str
    last_name: str
    email: str
    phone: str
    role: str
    status: str
    balance_naira: float
    is_email_verified: bool

    class Config:
        orm_mode = True


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


class AirtimeRequest(BaseModel):
    network: str        # mtn | airtel | glo | 9mobile
    phone: str
    amount: float

    @validator("amount")
    def min_amount(cls, v):
        if v < 50:
            raise ValueError("Minimum airtime is ₦50")
        return v


class DataRequest(BaseModel):
    network: str
    phone: str
    plan_code: str
    amount: float


class FundWalletRequest(BaseModel):
    amount: float
    gateway: str    # paystack | flutterwave


class AdminFundRequest(BaseModel):
    user_id: str
    action: str      # credit | debit
    amount: float
    note: Optional[str] = None


class TxResponse(BaseModel):
    id: str
    reference: str
    type: str
    status: str
    amount_naira: float
    description: str
    token: Optional[str] = None
    created_at: datetime

    class Config:
        orm_mode = True


# ════════════════════════════════════════════════════════════════
# AUTH HELPERS
# ════════════════════════════════════════════════════════════════
def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(user_id: str, role: str) -> str:
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    return jwt.encode({"sub": user_id, "role": role, "exp": expire}, SECRET_KEY, algorithm=ALGORITHM)


async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme)) -> User:
    try:
        payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("sub")
    except JWTError:
        raise HTTPException(401, "Invalid or expired token")
    user = await User.get(user_id)
    if not user:
        raise HTTPException(401, "User not found")
    if user.status == "blocked":
        raise HTTPException(403, "Account suspended")
    return user


async def require_admin(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role != "admin":
        raise HTTPException(403, "Admin access required")
    return current_user


def user_to_response(user: User) -> UserResponse:
    return UserResponse(
        id=str(user.id), first_name=user.first_name, last_name=user.last_name,
        email=user.email, phone=user.phone, role=user.role, status=user.status,
        balance_naira=user.balance_naira, is_email_verified=user.is_email_verified,
    )


def gen_ref(prefix="SVT") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12].upper()}"


# ════════════════════════════════════════════════════════════════
# CLUBKONNECT SERVICE
# ════════════════════════════════════════════════════════════════
CLUBKONNECT_NETWORK_MAP = {"mtn": "01", "glo": "02", "9mobile": "03", "airtel": "04"}


async def clubkonnect_buy_airtime(network: str, phone: str, amount: float, ref: str) -> dict:
    net_code = CLUBKONNECT_NETWORK_MAP.get(network.lower())
    if not net_code:
        return {"status": "error", "message": "Unknown network"}
    params = {
        "UserID": CLUBKONNECT_USER_ID,
        "APIKey": CLUBKONNECT_API_KEY,
        "MobileNetwork": net_code,
        "Amount": str(int(amount)),
        "MobileNumber": phone,
        "RequestID": ref,
    }
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            resp = await client.get(f"{CLUBKONNECT_BASE_URL}/APIAirtimeV1.asp", params=params)
            return {"status": "success", "raw": resp.text}
        except Exception as e:
            return {"status": "error", "message": str(e)}


async def clubkonnect_buy_data(network: str, phone: str, plan_code: str, ref: str) -> dict:
    net_code = CLUBKONNECT_NETWORK_MAP.get(network.lower())
    if not net_code:
        return {"status": "error", "message": "Unknown network"}
    params = {
        "UserID": CLUBKONNECT_USER_ID,
        "APIKey": CLUBKONNECT_API_KEY,
        "MobileNetwork": net_code,
        "DataPlan": plan_code,
        "MobileNumber": phone,
        "RequestID": ref,
    }
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            resp = await client.get(f"{CLUBKONNECT_BASE_URL}/APIDatabundleV1.asp", params=params)
            return {"status": "success", "raw": resp.text}
        except Exception as e:
            return {"status": "error", "message": str(e)}


# ════════════════════════════════════════════════════════════════
# PAYMENT SERVICES
# ════════════════════════════════════════════════════════════════
async def paystack_initialize(email: str, amount_kobo: int, reference: str, callback_url: str) -> dict:
    async with httpx.AsyncClient(timeout=20) as client:
        try:
            resp = await client.post(
                "https://api.paystack.co/transaction/initialize",
                json={"email": email, "amount": amount_kobo, "reference": reference, "callback_url": callback_url},
                headers={"Authorization": f"Bearer {PAYSTACK_SECRET_KEY}"},
            )
            data = resp.json()
            if data.get("status"):
                return {"success": True, "url": data["data"]["authorization_url"]}
            return {"success": False, "message": data.get("message")}
        except Exception as e:
            return {"success": False, "message": str(e)}


async def paystack_verify(reference: str) -> dict:
    async with httpx.AsyncClient(timeout=20) as client:
        try:
            resp = await client.get(
                f"https://api.paystack.co/transaction/verify/{reference}",
                headers={"Authorization": f"Bearer {PAYSTACK_SECRET_KEY}"},
            )
            data = resp.json()
            if data.get("status") and data["data"]["status"] == "success":
                return {"success": True, "amount_kobo": data["data"]["amount"]}
            return {"success": False}
        except Exception:
            return {"success": False}


# ════════════════════════════════════════════════════════════════
# DATABASE INIT
# ════════════════════════════════════════════════════════════════
async def init_db():
    client = AsyncIOMotorClient(MONGODB_URL)
    db = client[MONGODB_DB_NAME]
    await init_beanie(database=db, document_models=[User, Transaction, WalletFundingLog])


# ════════════════════════════════════════════════════════════════
# FASTAPI APP
# ════════════════════════════════════════════════════════════════
@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(title="SwiftVTU API", version="2.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root():
    return {"status": "ok", "app": "SwiftVTU", "version": "2.0.0"}


@app.get("/health")
async def health():
    return {"status": "healthy"}


# ── AUTH ROUTES ──────────────────────────────────────────────────
@app.post("/api/v1/auth/register", response_model=TokenResponse, status_code=201)
async def register(body: RegisterRequest):
    if await User.find_one(User.email == body.email.lower()):
        raise HTTPException(400, "Email already registered")
    if await User.find_one(User.phone == body.phone):
        raise HTTPException(400, "Phone already registered")

    user = User(
        first_name=body.first_name.strip(),
        last_name=body.last_name.strip(),
        email=body.email.lower().strip(),
        phone=body.phone.strip(),
        hashed_password=hash_password(body.password),
    )
    await user.insert()
    token = create_access_token(str(user.id), user.role)
    return TokenResponse(access_token=token, user=user_to_response(user))


@app.post("/api/v1/auth/login", response_model=TokenResponse)
async def login(body: LoginRequest):
    user = await User.find_one(User.email == body.email.lower())
    if not user or not verify_password(body.password, user.hashed_password):
        raise HTTPException(401, "Invalid email or password")
    if user.status == "blocked":
        raise HTTPException(403, "Account suspended")
    user.last_login = datetime.utcnow()
    await user.save()
    token = create_access_token(str(user.id), user.role)
    return TokenResponse(access_token=token, user=user_to_response(user))


@app.get("/api/v1/auth/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)):
    return user_to_response(current_user)


@app.post("/api/v1/auth/change-password")
async def change_password(body: dict, current_user: User = Depends(get_current_user)):
    if not verify_password(body.get("current_password", ""), current_user.hashed_password):
        raise HTTPException(400, "Current password incorrect")
    current_user.hashed_password = hash_password(body.get("new_password", ""))
    await current_user.save()
    return {"message": "Password updated"}


# ── VTU SERVICES ─────────────────────────────────────────────────
async def deduct_and_log(user: User, amount_naira: float, tx_type: str, description: str, extra: dict = None):
    amount_kobo = int(amount_naira * 100)
    if user.balance_kobo < amount_kobo:
        raise HTTPException(400, f"Insufficient balance. You have ₦{user.balance_naira:,.2f}")
    ref = gen_ref()
    user.balance_kobo -= amount_kobo
    await user.save()
    tx = Transaction(
        reference=ref, user_id=str(user.id), type=tx_type, status="pending",
        amount_kobo=amount_kobo, description=description, **(extra or {}),
    )
    await tx.insert()
    return tx, ref


@app.post("/api/v1/services/airtime", response_model=TxResponse)
async def buy_airtime(body: AirtimeRequest, current_user: User = Depends(get_current_user)):
    desc = f"{body.network.upper()} Airtime → {body.phone}"
    tx, ref = await deduct_and_log(current_user, body.amount, "airtime", desc,
                                    {"network": body.network, "phone": body.phone})

    result = await clubkonnect_buy_airtime(body.network, body.phone, body.amount, ref)

    if result.get("status") == "success" and "error" not in result.get("raw", "").lower():
        tx.status = "success"
        tx.completed_at = datetime.utcnow()
        tx.raw_response = {"raw": result.get("raw", "")}
        await tx.save()
    else:
        tx.status = "failed"
        tx.raw_response = result
        await tx.save()
        current_user.balance_kobo += tx.amount_kobo
        await current_user.save()
        raise HTTPException(400, f"Airtime purchase failed: {result.get('message', 'Provider error')}")

    return TxResponse(
        id=str(tx.id), reference=tx.reference, type=tx.type, status=tx.status,
        amount_naira=tx.amount_naira, description=tx.description, created_at=tx.created_at,
    )

@app.get("/api/v1/services/data/plans/{network_slug}")
async def get_data_plans(network_slug: str):
    slug = network_slug.strip().lower()
    if slug.startswith("mtn"):
        clubkonnect_key = "MTN"
    elif slug.startswith("glo"):
        clubkonnect_key = "Glo"
    elif slug.startswith("etisalat") or slug.startswith("9mobile"):
        clubkonnect_key = "m_9mobile"
    elif slug.startswith("airtel"):
        clubkonnect_key = "Airtel"
    else:
        raise HTTPException(404, "Unknown network")

    url = f"https://www.nellobytesystems.com/APIDatabundlePlansV2.asp?UserID={CLUBKONNECT_USER_ID}"
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url)
    data = resp.json()

    network_data = data.get("MOBILE_NETWORK", {}).get(clubkonnect_key)
    if not network_data:
        raise HTTPException(502, "Provider did not return plans for this network")

    products = network_data[0].get("PRODUCT", [])
    variations = [
        {
            "variation_code": p["PRODUCT_CODE"],
            "name": p["PRODUCT_NAME"],
            "variation_amount": round(float(p["PRODUCT_AMOUNT"]))
        }
        for p in products
    ]
    return {"content": {"varations": variations}}
@app.post("/api/v1/services/data", response_model=TxResponse)
async def buy_data(body: DataRequest, current_user: User = Depends(get_current_user)):
    desc = f"{body.network.upper()} Data → {body.phone}"
    tx, ref = await deduct_and_log(current_user, body.amount, "data", desc,
                                    {"network": body.network, "phone": body.phone})

    result = await clubkonnect_buy_data(body.network, body.phone, body.plan_code, ref)

    if result.get("status") == "success" and "error" not in result.get("raw", "").lower():
        tx.status = "success"
        tx.completed_at = datetime.utcnow()
        tx.raw_response = {"raw": result.get("raw", "")}
        await tx.save()
    else:
        tx.status = "failed"
        await tx.save()
        current_user.balance_kobo += tx.amount_kobo
        await current_user.save()
        raise HTTPException(400, f"Data purchase failed: {result.get('message', 'Provider error')}")

    return TxResponse(
        id=str(tx.id), reference=tx.reference, type=tx.type, status=tx.status,
        amount_naira=tx.amount_naira, description=tx.description, created_at=tx.created_at,
    )


# ── WALLET ───────────────────────────────────────────────────────
@app.post("/api/v1/wallet/fund/initiate")
async def initiate_funding(body: FundWalletRequest, request: Request, current_user: User = Depends(get_current_user)):
    ref = gen_ref("SVW")
    amount_kobo = int(body.amount * 100)
    await WalletFundingLog(user_id=str(current_user.id), reference=ref, gateway=body.gateway, amount_kobo=amount_kobo).insert()

    callback = f"{str(request.base_url).rstrip('/')}/api/v1/wallet/fund/verify/{ref}"

    if body.gateway == "paystack":
        result = await paystack_initialize(current_user.email, amount_kobo, ref, callback)
        if not result["success"]:
            raise HTTPException(400, result.get("message", "Payment init failed"))
        return {"payment_url": result["url"], "reference": ref}

    raise HTTPException(400, "Only paystack supported currently")


@app.get("/api/v1/wallet/fund/verify/{reference}")
async def verify_funding(reference: str):
    log = await WalletFundingLog.find_one(WalletFundingLog.reference == reference)
    if not log:
        raise HTTPException(404, "Funding session not found")
    if log.status == "success":
        return {"status": "already_verified"}

    result = await paystack_verify(reference)
    if not result.get("success"):
        log.status = "failed"
        await log.save()
        raise HTTPException(400, "Payment verification failed")

    user = await User.get(log.user_id)
    user.balance_kobo += result["amount_kobo"]
    await user.save()

    await Transaction(
        reference=reference, user_id=str(user.id), type="wallet_funding", status="success",
        amount_kobo=result["amount_kobo"], description="Wallet Funding via Paystack",
        gateway="paystack", completed_at=datetime.utcnow(),
    ).insert()

    log.status = "success"
    await log.save()
    return {"status": "success", "new_balance": user.balance_naira}


@app.get("/api/v1/wallet/transactions")
async def get_transactions(limit: int = 20, current_user: User = Depends(get_current_user)):
    txs = await Transaction.find(Transaction.user_id == str(current_user.id)).sort(-Transaction.created_at).limit(limit).to_list()
    return {
        "total": len(txs),
        "transactions": [
            {"id": str(t.id), "reference": t.reference, "type": t.type, "status": t.status,
             "amount_naira": t.amount_naira, "description": t.description, "created_at": t.created_at}
            for t in txs
        ],
    }


# ── ADMIN ────────────────────────────────────────────────────────
@app.get("/api/v1/admin/users")
async def admin_list_users(limit: int = 100, _: User = Depends(require_admin)):
    users = await User.find().sort(-User.created_at).limit(limit).to_list()
    return {
        "total": len(users),
        "users": [
            {"id": str(u.id), "name": u.full_name, "email": u.email, "phone": u.phone,
             "status": u.status, "balance_naira": u.balance_naira, "created_at": u.created_at}
            for u in users
        ],
    }


@app.patch("/api/v1/admin/users/{user_id}/status")
async def admin_update_status(user_id: str, body: dict, _: User = Depends(require_admin)):
    user = await User.get(user_id)
    if not user:
        raise HTTPException(404, "User not found")
    user.status = body.get("status", user.status)
    await user.save()
    return {"message": f"User is now {user.status}"}


@app.delete("/api/v1/admin/users/{user_id}")
async def admin_delete_user(user_id: str, _: User = Depends(require_admin)):
    user = await User.get(user_id)
    if not user:
        raise HTTPException(404, "User not found")
    await user.delete()
    return {"message": "User deleted"}


@app.get("/api/v1/admin/transactions")
async def admin_list_tx(limit: int = 100, _: User = Depends(require_admin)):
    txs = await Transaction.find().sort(-Transaction.created_at).limit(limit).to_list()
    return {
        "total": len(txs),
        "transactions": [
            {"id": str(t.id), "reference": t.reference, "user_id": t.user_id, "type": t.type,
             "status": t.status, "amount_naira": t.amount_naira, "description": t.description,
             "created_at": t.created_at}
            for t in txs
        ],
    }


@app.post("/api/v1/admin/wallet/fund")
async def admin_fund_wallet(body: AdminFundRequest, admin: User = Depends(require_admin)):
    user = await User.get(body.user_id)
    if not user:
        raise HTTPException(404, "User not found")
    amount_kobo = int(body.amount * 100)
    if body.action == "credit":
        user.balance_kobo += amount_kobo
    else:
        if user.balance_kobo < amount_kobo:
            raise HTTPException(400, "Insufficient user balance")
        user.balance_kobo -= amount_kobo
    await user.save()

    await Transaction(
        reference=gen_ref("ADM"), user_id=str(user.id),
        type="admin_credit" if body.action == "credit" else "admin_debit",
        status="success", amount_kobo=amount_kobo,
        description=body.note or f"Admin {body.action}", completed_at=datetime.utcnow(),
    ).insert()

    return {"message": f"₦{body.amount:,.2f} {body.action}ed", "new_balance": user.balance_naira}


@app.get("/api/v1/admin/analytics/overview")
async def admin_analytics(_: User = Depends(require_admin)):
    users = await User.find().to_list()
    txs = await Transaction.find().to_list()
    success_txs = [t for t in txs if t.status == "success"]
    revenue = sum(t.amount_kobo for t in success_txs if t.type != "wallet_funding") / 100

    return {
        "total_users": len(users),
        "active_users": sum(1 for u in users if u.status == "active"),
        "total_transactions": len(txs),
        "successful_transactions": len(success_txs),
        "failed_transactions": sum(1 for t in txs if t.status == "failed"),
        "total_revenue_naira": revenue,
        "revenue_by_type": {},
        "last_7_days": [],
    }
