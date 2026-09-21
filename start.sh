
echo "Starting IBKR Client Portal Gateway and Flask webapp..."
# Start the IBKR Client Portal Gateway
(cd gateway && sh bin/run.sh root/conf.yaml) &

echo "Waiting for IBKR Client Portal Gateway to start..."
# --- Flask webapp on port 5056 ---
(cd webapp && python3 -m venv venv && . venv/bin/activate && venv/bin/pip install flask requests python-dotenv && flask --app app run --debug -p 5056 -h 0.0.0.0) &

# --- FastAPI algo trade app on port 4002 ---
(cd ibkr-algo-tradeapp && python3 -m venv venv && . venv/bin/activate && venv/bin/pip install -r requirements.txt && uvicorn main:app --host 0.0.0.0 --port 4002 --reload) &

# --- ngrok tunnel exposing the Flask webhook endpoint on port 5056 ---
if [ -n "$NGROK_AUTHTOKEN" ]; then
    ngrok config add-authtoken "$NGROK_AUTHTOKEN"
fi
ngrok http 5056 --log=stdout &

# Keep the container alive and let a failure of any background job stop it
wait -n