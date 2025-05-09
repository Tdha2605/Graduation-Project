from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI()

# Danh sách thiết bị hợp lệ: macAddress -> password
VALID_CREDENTIALS = {
    "DC:A6:32:B8:5B:DB": "6e83f8f27c9a79c788271459c76794c8515ea337ad86eb6eb5cc88681c40fc28",
    "D8:3A:DD:51:09:02": "49fb5ec0d7e73bf8a3efffb3de9321f92b10fbf0702d8ffa8e9356c2e57cec89"
}

@app.post("/api/devicecomm/getmqtttoken")
async def get_mqtt_token(request: Request):
    body = await request.json()
    mac_address = body.get("macAddress")
    
    if not mac_address:
        return JSONResponse(
            status_code=400,
            content={"code": "ERROR", "message": "Missing macAddress"}
        )
    
    # Nếu mac_address hợp lệ thì trả về token (ở đây mình giả lập token luôn)
    if mac_address in VALID_CREDENTIALS:
        fake_token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.FakeToken"
        return {
            "code": "OK",
            "message": "Success",
            "data": {
                "token": VALID_CREDENTIALS[mac_address],
                "username": mac_address
            }
        }
    else:
        return JSONResponse(
            status_code=401,
            content={"code": "UNAUTHORIZED", "message": "MAC address not registered"}
        )
