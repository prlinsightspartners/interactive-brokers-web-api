# Start the IBKR Client Portal Gateway 
cd gateway && sh bin/run.sh root/conf.yaml &

# --- Flask webapp on port 5056 ---
cd webapp && python3 -m venv venv && . venv/bin/activate && venv/bin/pip install -r requirements.txt
flask --app app run --debug -p 5056 -h 0.0.0.0 &

# --- FastAPI algo trade app on port 4002 ---
cd ibkr-algo-tradeapp && python3 -m venv venv && . venv/bin/activate && venv/bin/pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 4002  --reload