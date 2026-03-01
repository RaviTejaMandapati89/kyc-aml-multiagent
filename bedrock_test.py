import boto3
import json

client = boto3.client('bedrock-runtime', region_name='eu-west-2')

response = client.invoke_model(
    modelId='anthropic.claude-haiku-4-5-20251001-v1:0',
    body=json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 100,
        "messages": [
            {
                "role": "user",
                "content": "Reply with exactly this: AWS Bedrock connection successful."
            }
        ]
    })
)

result = json.loads(response['body'].read())
print(result['content'][0]['text'])