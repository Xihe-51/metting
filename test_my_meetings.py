import requests

r = requests.post('http://localhost:8000/api/v1/auth/login', json={'username':'羲和盒盒盒','password':'123456'})
print('Login:', r.status_code)
token = r.json()['data']['access_token']

r2 = requests.get('http://localhost:8000/api/v1/meetings/my', headers={'Authorization': 'Bearer ' + token})
print('My meetings:', r2.status_code)
print(r2.json())