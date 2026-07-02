import os
from dotenv import load_dotenv
from supabase import create_client

load_dotenv("C:/Dev/WheelLife/WheelLifeMS/.env")

url = os.environ.get("SUPABASE_URL")
key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
import httpx
from supabase import ClientOptions
http_client = httpx.Client(verify=False)
options = ClientOptions(httpx_client=http_client)
supabase = create_client(url, key, options=options)

res = supabase.table("system_settings").select("*").execute()
print(res.data)
