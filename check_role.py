from app import app
from models import User
with app.app_context():
    u = User.query.filter_by(email='abhinav.entegrasources@gmail.com').first()
    if u:
        print(f"VERIFIED: {u.email} ROLE: {u.role}")
    else:
        print("NOT FOUND")
