
import boto3
from botocore import UNSIGNED
from botocore.config import Config
import json

BUCKET = "alextrywebsite"
AWS_REGION = "us-east-2"

print(f"Testing access to s3://{BUCKET} in {AWS_REGION}...")

try:
    s3_client = boto3.client("s3", region_name=AWS_REGION, config=Config(signature_version=UNSIGNED))
    
    # Try to list objects
    print("Listing objects in db/data/ ...")
    resp = s3_client.list_objects_v2(Bucket=BUCKET, Prefix="db/data/", MaxKeys=20)
    
    if 'Contents' in resp:
        print(f"Found {len(resp['Contents'])} objects.")
        for item in resp['Contents']:
            print(f" - {item['Key']}")
    else:
        print("Bucket accessed, but no objects found in 'db/data/' prefix.")
        
    print("SUCCESS: Anonymous read access works.")

except Exception as e:
    print(f"FAILURE: {e}")
