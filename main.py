"""
Deployment entrypoint.

FastAPI Cloud root pe `app` dhoondta hai, is liye web/app.py se wahi import
kar dete hain — koi alag copy nahi, taake dono kabhi alag na hon.
"""
from web.app import app  # noqa: F401
