import boto3
from botocore.exceptions import ClientError

def set_cors():
    bucket_name = "alextrywebsite"
    
    cors_configuration = {
        'CORSRules': [{
            'AllowedHeaders': ['*'],
            'AllowedMethods': ['GET', 'PUT', 'POST', 'HEAD'],
            'AllowedOrigins': ['*'],  # Allow all domains (adjust if you have a specific domain)
            'ExposeHeaders': ['ETag', 'x-amz-server-side-encryption', 'x-amz-request-id', 'x-amz-id-2'],
            'MaxAgeSeconds': 3000
        }]
    }

    try:
        s3 = boto3.client('s3', region_name='us-east-2')
        s3.put_bucket_cors(Bucket=bucket_name, CORSConfiguration=cors_configuration)
        print(f"SUCCESS: Updated CORS for bucket '{bucket_name}'.")
        print("Your browser should now be able to upload directly.")
    except ClientError as e:
        print(f"ERROR: Could not update CORS. {e}")
        print("Ensure this machine has s3:PutBucketCors permission.")

if __name__ == "__main__":
    set_cors()
