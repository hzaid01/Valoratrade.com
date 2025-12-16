#!/bin/bash

echo "Starting CryptoBot Frontend..."

if [ ! -d "node_modules" ]; then
    echo "Installing dependencies..."
    npm install
fi

echo "Starting development server..."
npm run dev
