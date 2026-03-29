from app import app
from models import Team
with app.app_context():
    for t in Team.query.all():
        print(f"'{t.name}'")
