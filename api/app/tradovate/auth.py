import base64
import hashlib
import hmac
import time

# these come from tradovate's own website code (trader.tradovate.com), not from their
# official api. it is not real cryptography, it is just what their web client sends.
# because of this we can log in with only a username and a password.
WEB_APP_ID = "tradovate_trader(web)"
WEB_SEC_KEY = "035a1259-11e7-485a-aeae-9b6016579351"


# rotate the password left by (username length mod password length), reverse it, base64 it
def encode_password(username, password):
    offset = len(username) % len(password)
    rotated = password[offset:] + password[:offset]
    return base64.b64encode(rotated[::-1].encode()).decode()


# chl is just the current time in a weird unit, tradovate does not check it against anything
def make_chl():
    return str(int(time.time() * 1000) - 1_581_000_000_000)


# hmac over the raw (not encoded) password
def make_sec(chl, device_id, username, password, app_id):
    message = f"{chl}{device_id}{username}{password}{app_id}"
    return hmac.new(WEB_SEC_KEY.encode(), message.encode(), hashlib.sha256).hexdigest()


# the json we post to /auth/accesstokenrequest
def build_login_payload(config, app_version, p_ticket=None):
    if config.auth_mode == "api_key":
        # official login, documented by tradovate. needs an api key from your account
        payload = {
            "name": config.username,
            "password": config.password,
            "appId": "localfill",
            "appVersion": app_version,
            "cid": int(config.cid) if config.cid.isdigit() else config.cid,
            "sec": config.api_secret,
            "deviceId": config.device_id,
        }
    else:
        chl = make_chl()
        payload = {
            "name": config.username,
            "password": encode_password(config.username, config.password),
            "appId": WEB_APP_ID,
            "appVersion": app_version,
            "cid": "1",
            "sec": make_sec(chl, config.device_id, config.username, config.password, WEB_APP_ID),
            "environment": "demo" if config.demo else "live",
            "enc": True,
            "deviceId": config.device_id,
            "chl": chl,
        }
    # tradovate slows down repeated logins, when that happens we wait and send this back
    if p_ticket:
        payload["p-ticket"] = p_ticket
    return payload
