# Cloud Scheduler Setup for Continuous Training
# 
# This configures automatic model retraining every 15 minutes
# to keep the models updated with the latest market structure.

# ============================================================
# SETUP INSTRUCTIONS
# ============================================================

# 1. Deploy the updated backend to Cloud Run:
#    cd backend
#    gcloud run deploy trading-api --source . --region europe-west1

# 2. Create a Cloud Scheduler job that calls the training endpoint:
#
#    gcloud scheduler jobs create http training-job-15min \
#      --location=europe-west1 \
#      --schedule="*/15 * * * *" \
#      --uri="https://trading-api-kgibudh5wq-el.a.run.app/api/training/trigger" \
#      --http-method=POST \
#      --headers="Content-Type=application/json" \
#      --message-body='{"symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT"], "force": false}' \
#      --oidc-service-account-email=YOUR_SERVICE_ACCOUNT@PROJECT.iam.gserviceaccount.com \
#      --oidc-token-audience="https://trading-api-kgibudh5wq-el.a.run.app"

# 3. Or create via Google Cloud Console:
#    - Go to Cloud Scheduler: https://console.cloud.google.com/cloudscheduler
#    - Click "Create Job"
#    - Name: training-job-15min
#    - Frequency: */15 * * * *
#    - Timezone: UTC
#    - Target: HTTP
#    - URL: https://trading-api-kgibudh5wq-el.a.run.app/api/training/trigger
#    - HTTP Method: POST
#    - Headers: Content-Type: application/json
#    - Body: {"symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT"], "force": false}
#    - Auth: Add OIDC token with your service account

# ============================================================
# ENDPOINTS
# ============================================================
#
# POST /api/training/trigger
#   - Triggers training in background
#   - Returns immediately
#   - Body: {"symbols": ["BTCUSDT", "ETHUSDT"], "force": false}
#
# GET /api/training/status
#   - Returns current training status
#   - Shows: is_running, last_run, last_result, run_count
#
# POST /api/training/run-sync
#   - Runs training synchronously (may timeout)
#   - Use for Cloud Run Jobs instead of Scheduler

# ============================================================
# ALTERNATIVE: CLOUD RUN JOBS (for longer training)
# ============================================================
#
# If training takes longer than 10 minutes, use Cloud Run Jobs:
#
# 1. Create a job:
#    gcloud run jobs create training-job \
#      --image=gcr.io/PROJECT/trading-api \
#      --region=europe-west1 \
#      --command="python,-m,jobs.retrain" \
#      --set-env-vars="SYMBOLS=BTCUSDT,ETHUSDT,SOLUSDT"
#
# 2. Schedule it:
#    gcloud scheduler jobs create http training-job-trigger \
#      --location=europe-west1 \
#      --schedule="*/15 * * * *" \
#      --uri="https://europe-west1-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/PROJECT/jobs/training-job:run" \
#      --http-method=POST \
#      --oauth-service-account-email=YOUR_SERVICE_ACCOUNT@PROJECT.iam.gserviceaccount.com
