# aliyun_token.py - 阿里云Token获取（带缓存和完整错误输出）
import hmac
import hashlib
import base64
import time
import uuid
import requests
from config import ALIYUN_ACCESS_KEY_ID, ALIYUN_ACCESS_KEY_SECRET

_token_cache = {"token": "", "expires": 0}

def get_token() -> str:
    """获取阿里云NLS Token，带缓存（Token有效期约24小时，缓存5分钟）"""
    now = time.time()
    if _token_cache["token"] and now < _token_cache["expires"]:
        return _token_cache["token"]

    if not ALIYUN_ACCESS_KEY_ID or not ALIYUN_ACCESS_KEY_SECRET:
        print("[阿里云Token] 错误: ACCESS_KEY 未配置，请在 config.py 中填写")
        return ""

    url = "https://nls-meta.cn-shanghai.aliyuncs.com/"
    timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    nonce = str(uuid.uuid4())

    params = {
        "AccessKeyId": ALIYUN_ACCESS_KEY_ID,
        "Action": "CreateToken",
        "Format": "JSON",
        "RegionId": "cn-shanghai",
        "SignatureMethod": "HMAC-SHA1",
        "SignatureNonce": nonce,
        "SignatureVersion": "1.0",
        "Timestamp": timestamp,
        "Version": "2019-02-28",
    }

    sorted_params = "&".join(
        f"{k}={requests.utils.quote(str(v), safe='')}"
        for k, v in sorted(params.items())
    )
    string_to_sign = f"GET&%2F&{requests.utils.quote(sorted_params, safe='')}"

    secret = ALIYUN_ACCESS_KEY_SECRET + "&"
    signature = base64.b64encode(
        hmac.new(secret.encode(), string_to_sign.encode(), hashlib.sha1).digest()
    ).decode()

    params["Signature"] = signature

    try:
        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()
    except Exception as e:
        print(f"[阿里云Token] 请求失败: {e}")
        return ""

    # 输出详细信息便于调试
    if resp.status_code != 200:
        print(f"[阿里云Token] HTTP {resp.status_code}: {resp.text}")
        return ""

    token = data.get("Token", {}).get("Id", "")
    if not token:
        print(f"[阿里云Token] 返回数据中未找到Token: {data}")
        # 有些错误返回格式不同
        if "Message" in data:
            print(f"[阿里云Token] 错误信息: {data['Message']}")
        if "Code" in data:
            print(f"[阿里云Token] 错误码: {data['Code']}")
        return ""

    # 缓存5分钟（Token实际有效期更长，但缓存5分钟够用）
    _token_cache["token"] = token
    _token_cache["expires"] = now + 300
    print(f"[阿里云Token] 获取成功 (前20字符: {token[:20]}...)")
    return token
