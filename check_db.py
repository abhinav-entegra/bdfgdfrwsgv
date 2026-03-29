    u = User.query.filter_by(email='abhinav.entegrasources@gmail.com').first()
    if u: print(f"CHECK: {u.email} is {u.role}")
