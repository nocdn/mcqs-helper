#!/bin/bash

echo "Building docker image..."
docker build -t mcqs-helper-img .
echo "Running docker container..."
docker run -d -p 7480:7480 --name mcqs-helper --env-file .env mcqs-helper-img