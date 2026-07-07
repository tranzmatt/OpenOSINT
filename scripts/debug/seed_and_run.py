import os
import uvicorn
from cloud import db
from cloud.main import app

db._MEMORY_CUSTOMERS["FAKE_KEY_OK"] = db.Customer(
    api_key="FAKE_KEY_OK",
    polar_customer_id="polar_fake_ok",
    credits=100,
    plan="starter",
)
db._MEMORY_BY_POLAR_ID["polar_fake_ok"] = "FAKE_KEY_OK"

db._MEMORY_CUSTOMERS["FAKE_KEY_TAKEN"] = db.Customer(
    api_key="FAKE_KEY_TAKEN",
    polar_customer_id="polar_fake_taken",
    credits=50,
    plan="payg",
)
db._MEMORY_USERS[999999] = db.User(
    id=999999,
    provider="synthetic",
    provider_user_id="ghost",
    email=None,
    polar_customer_id="polar_fake_taken",
    customer_api_key="FAKE_KEY_TAKEN",
)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
