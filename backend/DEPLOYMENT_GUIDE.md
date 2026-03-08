# Production Backend Deployment Guide

## Prerequisites

- Google Cloud SDK installed (`gcloud`)
- Firebase Admin SDK credentials
- Docker installed (for local builds)
- Project ID: `crypto-app-3c146`

---

## 1. Environment Variables

### Required for Cloud Run

| Variable | Description | Required |
|----------|-------------|----------|
| `GOOGLE_APPLICATION_CREDENTIALS` | Path to Firebase service account JSON | Auto in Cloud Run |
| `FIREBASE_SERVICE_ACCOUNT_JSON` | JSON string of service account (alternative) | Yes in Cloud Run |
| `ENCRYPTION_SECRET` | Fernet key for API key encryption | Yes |
| `ALLOWED_ORIGINS` | CORS origins (comma-separated) | Yes |
| `PORT` | Server port (default: 8080) | Auto |

### Generate Encryption Secret
```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

---

## 2. IAM Roles Required

### Service Account Roles
```bash
PROJECT_ID=crypto-app-3c146
SA_EMAIL=trading-api-sa@${PROJECT_ID}.iam.gserviceaccount.com

# Create service account
gcloud iam service-accounts create trading-api-sa \
  --display-name="Trading API Service Account" \
  --project=${PROJECT_ID}

# Grant Firestore access
gcloud projects add-iam-policy-binding ${PROJECT_ID} \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/datastore.user"

# Grant Cloud Storage access
gcloud projects add-iam-policy-binding ${PROJECT_ID} \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/storage.objectAdmin"

# Grant Cloud Run invoker (for Scheduler)
gcloud projects add-iam-policy-binding ${PROJECT_ID} \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/run.invoker"

# Grant Secret Manager access
gcloud projects add-iam-policy-binding ${PROJECT_ID} \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/secretmanager.secretAccessor"
```

---

## 3. Deploy to Cloud Run

### Build and Deploy
```bash
cd project/backend

# Deploy to asia-south1 (closest to user, Binance-friendly)
gcloud run deploy trading-api \
  --source . \
  --region asia-south1 \
  --platform managed \
  --allow-unauthenticated \
  --service-account trading-api-sa@crypto-app-3c146.iam.gserviceaccount.com \
  --set-env-vars "ALLOWED_ORIGINS=https://crypto-app-3c146.web.app,https://crypto-app-3c146.firebaseapp.com" \
  --set-env-vars "ENCRYPTION_SECRET=YOUR_ENCRYPTION_SECRET" \
  --memory 2Gi \
  --cpu 2 \
  --timeout 300 \
  --min-instances 0 \
  --max-instances 3 \
  --concurrency 80
```

### Using Secret Manager (Recommended)
```bash
# Create secret for encryption key
echo -n "YOUR_ENCRYPTION_SECRET" | gcloud secrets create encryption-secret \
  --data-file=- \
  --replication-policy=automatic

# Deploy with secret reference
gcloud run deploy trading-api \
  --source . \
  --region asia-south1 \
  --set-secrets "ENCRYPTION_SECRET=encryption-secret:latest"
```

---

## 4. Cloud Scheduler Jobs

### Job 1: Data Ingestion (Every 15 minutes)
```bash
gcloud scheduler jobs create http data-ingestion-15min \
  --location=asia-south1 \
  --schedule="*/15 * * * *" \
  --uri="https://trading-api-XXXXX-el.a.run.app/api/training/ingest-data" \
  --http-method=POST \
  --headers="Content-Type=application/json" \
  --message-body='{"symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT"]}' \
  --oidc-service-account-email=trading-api-sa@crypto-app-3c146.iam.gserviceaccount.com \
  --attempt-deadline=120s \
  --time-zone="UTC"
```

### Job 2: Training (Every 6 hours, conditional)
```bash
gcloud scheduler jobs create http training-6h \
  --location=asia-south1 \
  --schedule="0 */6 * * *" \
  --uri="https://trading-api-XXXXX-el.a.run.app/api/training/trigger" \
  --http-method=POST \
  --headers="Content-Type=application/json" \
  --message-body='{"symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT"], "force": false}' \
  --oidc-service-account-email=trading-api-sa@crypto-app-3c146.iam.gserviceaccount.com \
  --attempt-deadline=600s \
  --time-zone="UTC"
```

---

## 5. Firestore Collections

The system will auto-create these collections:

| Collection | Purpose |
|------------|---------|
| `system_state` | State machine per symbol |
| `job_locks` | Training job locks |
| `job_retries` | Retry count tracking |
| `orphan_jobs` | Failed job cleanup log |
| `state_transitions` | State change audit log |
| `candles_1h` | Accumulated 1H candle data |
| `predictions` | Forward prediction records |
| `demotion_events` | Champion demotion audit |

---

## 6. Production Verification Checklist

### 6.1 Health Check
```bash
SERVICE_URL=https://trading-api-XXXXX-el.a.run.app

# Health endpoint
curl ${SERVICE_URL}/health
# Expected: {"status": "healthy", ...}
```

### 6.2 Data Ingestion
```bash
# Trigger ingestion
curl -X POST ${SERVICE_URL}/api/training/ingest-data

# Check stats
curl ${SERVICE_URL}/api/training/data-stats
# Expected: candle_count > 0
```

### 6.3 System State
```bash
# Check state machine
curl ${SERVICE_URL}/api/training/system-state
# Expected: state per symbol
```

### 6.4 Training Trigger
```bash
# Force training (with data)
curl -X POST ${SERVICE_URL}/api/training/trigger \
  -H "Content-Type: application/json" \
  -d '{"force": true}'
```

### 6.5 Training Status
```bash
curl ${SERVICE_URL}/api/training/status
# Expected: is_running: false after completion
```

### 6.6 Model Registry
```bash
curl ${SERVICE_URL}/api/admin/models
# Expected: models array (may be empty initially)
```

---

## 7. Logging

### Enable Structured Logging
```bash
gcloud logging read "resource.type=cloud_run_revision AND resource.labels.service_name=trading-api" \
  --limit 50 \
  --format "table(timestamp, severity, jsonPayload.message)"
```

### Filter Training Logs
```bash
gcloud logging read "resource.labels.service_name=trading-api AND jsonPayload.message:training" \
  --limit 20
```

---

## 8. Rollback

### Rollback to Previous Revision
```bash
# List revisions
gcloud run revisions list --service trading-api --region asia-south1

# Rollback
gcloud run services update-traffic trading-api \
  --to-revisions PREVIOUS_REVISION=100 \
  --region asia-south1
```

---

## Quick Deploy Commands

```bash
# Full deployment sequence
PROJECT_ID=crypto-app-3c146
REGION=asia-south1
SERVICE_NAME=trading-api

# 1. Deploy
cd project/backend
gcloud run deploy ${SERVICE_NAME} \
  --source . \
  --region ${REGION} \
  --allow-unauthenticated \
  --memory 2Gi \
  --cpu 2

# 2. Get URL
SERVICE_URL=$(gcloud run services describe ${SERVICE_NAME} --region ${REGION} --format='value(status.url)')
echo "Deployed to: ${SERVICE_URL}"

# 3. Verify
curl ${SERVICE_URL}/health

# 4. Create scheduler jobs (update URL first!)
# Data ingestion every 15 min
gcloud scheduler jobs create http data-ingestion-15min \
  --location=${REGION} \
  --schedule="*/15 * * * *" \
  --uri="${SERVICE_URL}/api/training/ingest-data" \
  --http-method=POST

# Training every 6 hours
gcloud scheduler jobs create http training-6h \
  --location=${REGION} \
  --schedule="0 */6 * * *" \
  --uri="${SERVICE_URL}/api/training/trigger" \
  --http-method=POST \
  --message-body='{"force": false}'

# 5. Test data ingestion
curl -X POST ${SERVICE_URL}/api/training/ingest-data
```
