#!/bin/bash
# deploy.sh: uploads the website files from this folder to the S3 bucket.
# Run it from the project folder with:  ./deploy.sh

BUCKET="airbnbmidterm-s3"   # the S3 bucket that hosts the site
PROFILE="airbnb-search"     # AWS CLI profile with access to the bucket

# Copy only files that changed, and skip things the website doesn't need
aws s3 sync . "s3://$BUCKET" \
  --profile "$PROFILE" \
  --region us-east-1 \
  --exclude "*" \
  --include "index.html" \
  --include "styles.css" \
  --include "searchbar.js" \
  --include "account.js" \
  --include "auth.js"
