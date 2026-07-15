from motor.motor_asyncio import AsyncIOMotorClient
from beanie import init_beanie
from app.config import settings
import logging

logger = logging.getLogger(__name__)


async def init_db():
    """Initialize MongoDB connection and Beanie ODM."""
    from app.models.user import User
    from app.models.transaction import Transaction
    from app.models.wallet import WalletFundingLog

    client = AsyncIOMotorClient(settings.MONGODB_URL)
    db = client[settings.MONGODB_DB_NAME]

    await init_beanie(
        database=db,
        document_models=[User, Transaction, WalletFundingLog],
    )

    # Create indexes
    await User.get_motor_collection().create_index("email", unique=True)
    await User.get_motor_collection().create_index("phone", unique=True)
    await Transaction.get_motor_collection().create_index("reference", unique=True)
    await Transaction.get_motor_collection().create_index("user_id")
    await Transaction.get_motor_collection().create_index("created_at")

    logger.info(f"✅ Connected to MongoDB: {settings.MONGODB_DB_NAME}")
