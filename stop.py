import requests

# nodes
for i in range(36):
    try:
        requests.get(f"http://localhost:{8000 + i}/exit/")
    except:
        print(f"Node{i} is stopped.")