@echo off

echo Starting CryptoBot Frontend...

if not exist "node_modules" (
    echo Installing dependencies...
    call npm install
)

echo Starting development server...
call npm run dev
