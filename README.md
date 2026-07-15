# SwiftVTU — Backend API

FastAPI + MongoDB backend for the SwiftVTU platform.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Framework | FastAPI 0.111 |
| Database | MongoDB (via Motor + Beanie ODM) |
| Auth | JWT (access + refresh tokens) |
| Payments | Paystack + Flutterwave |
| VTU Provider | VTpass |
| Passwords | bcrypt via passlib |

---

## Project Structure

```
swiftvtu/
├── app/
│   ├── main.py              # FastAPI app entry point
│   ├── config.py            # Settings from .env
│   ├── database.py          # MongoDB init
│   ├── models/
│   │   ├── user.py          # User document
│   │   ├── transaction.py   # Transaction document
│   │   └── wallet.py        # Wallet funding log
│   ├── schemas/
│   │   ├── auth.py          # Auth request/response schemas
│   │   └── services.py      # VTU service schemas
│   ├── services/
│   │   ├── auth_service.py  # JWT + password helpers
│   │   ├── vtpass_service.py# VTpass API calls
│   │   └── payment_service.py# Paystack + Flutterwave
│   ├── routers/
│   │   ├── auth.py          # /api/v1/auth/*
│   │   ├── services.py      # /api/v1/services/*
│   │   ├── wallet.py        # /api/v1/wallet/*
│   │   └── admin.py         # /api/v1/admin/*
│   └── middleware/
│       └── rate_limit.py    # slowapi rate limiter
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── .env.example
```

---

## Quick Start

### 1. Clone & configure

```bash
git clone <your-repo>
cd swiftvtu
cp .env.example .env
# Edit .env with your API keys
```

### 2. Run with Docker (recommended)

```bash
docker-compose up --build
```

- API:            http://localhost:8000
- Swagger docs:   http://localhost:8000/docs
- MongoDB UI:     http://localhost:8081  (admin / admin123)

### 3. Run locally (without Docker)

```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt

# Make sure MongoDB is running locally
uvicorn app.main:app --reload --port 8000
```

---

## API Keys You Need

### VTpass
1. Register at https://vtpass.com
2. Go to Settings → API → generate keys
3. Use sandbox URL for testing: `https://sandbox.vtpass.com/api`

### Paystack
1. Register at https://paystack.com
2. Dashboard → Settings → API Keys & Webhooks
3. Copy Secret Key and Public Key

### Flutterwave
1. Register at https://flutterwave.com
2. Dashboard → Settings → API
3. Copy Secret Key and Public Key

---

## API Endpoints

### Auth  `/api/v1/auth`
| Method | Path | Description |
|--------|------|-------------|
| POST | `/register` | Create account |
| POST | `/login` | Login → tokens |
| POST | `/refresh` | Refresh access token |
| GET  | `/me` | Get current user |
| PATCH | `/me` | Update profile |
| POST | `/change-password` | Change password |
| POST | `/forgot-password` | Request reset |

### VTU Services  `/api/v1/services`
| Method | Path | Description |
|--------|------|-------------|
| POST | `/airtime` | Buy airtime |
| GET  | `/data/plans/{service_id}` | Get data plans |
| POST | `/data` | Buy data bundle |
| POST | `/electricity/verify-meter` | Verify meter |
| POST | `/electricity` | Pay electricity |
| POST | `/cable-tv/verify` | Verify smartcard |
| GET  | `/cable-tv/plans/{service_id}` | Get TV plans |
| POST | `/cable-tv` | Subscribe cable TV |
| POST | `/exam-pins` | Buy exam pins |
| POST | `/betting` | Fund betting wallet |

### Wallet  `/api/v1/wallet`
| Method | Path | Description |
|--------|------|-------------|
| POST | `/fund/initiate` | Start wallet funding |
| GET  | `/fund/verify/{gateway}/{ref}` | Verify payment |
| POST | `/webhook/paystack` | Paystack webhook |
| POST | `/webhook/flutterwave` | Flutterwave webhook |
| GET  | `/transactions` | My transaction history |

### Admin  `/api/v1/admin`
| Method | Path | Description |
|--------|------|-------------|
| GET  | `/users` | List all users |
| GET  | `/users/{id}` | Get user detail |
| PATCH | `/users/{id}/status` | Block / unblock |
| DELETE | `/users/{id}` | Delete user |
| GET  | `/transactions` | All transactions |
| POST | `/wallet/fund` | Credit / debit wallet |
| GET  | `/analytics/overview` | Platform stats |

---

## VTpass Service IDs Reference

### Airtime
- `mtn` `airtel` `glo` `etisalat`

### Data
- `mtn-data` `airtel-data` `glo-sme-data` `etisalat-data`

### Electricity (DISCOs)
- `ikeja-electric` `eko-electric` `abuja-electric`
- `phed` `eedc` `ibedc` `kedco` `aedc`

### Cable TV
- `dstv` `gotv` `startimes`

### Exam Pins
- `waec` `waec-registration` `neco` `nabteb`

### Betting
- `bet9ja` `betking` `1xbet` `sportybet`

---

## Connecting the Frontend

In `vtu-app.html`, replace the demo JS logic with real `fetch()` calls:

```javascript
// Example: buy airtime
const res = await fetch('http://localhost:8000/api/v1/services/airtime', {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
    'Authorization': `Bearer ${localStorage.getItem('access_token')}`
  },
  body: JSON.stringify({
    network: 'mtn',
    phone: '08012345678',
    amount: 500
  })
});
const data = await res.json();
```

---

## Production Checklist

- [ ] Change `SECRET_KEY` to a strong random value
- [ ] Switch VTpass to production URL (`https://vtpass.com/api`)
- [ ] Switch Paystack/Flutterwave to live keys
- [ ] Add HTTPS (use nginx or a cloud load balancer)
- [ ] Set `APP_ENV=production`
- [ ] Set `ALLOWED_ORIGINS` to your actual frontend domain
- [ ] Configure MongoDB Atlas (or a secured self-hosted instance)
- [ ] Set up webhook URLs in Paystack and Flutterwave dashboards
- [ ] Enable MongoDB authentication
