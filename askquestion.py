from datetime import datetime, timedelta
import json, os
import random
import boto3
from enum import Enum
from pydantic import BaseModel
from typing import Dict, List, Optional, Any

# AWS service clients
s3 = boto3.client('s3')
bedrock_runtime = boto3.client('bedrock-runtime')
dynamodb = boto3.resource('dynamodb')

# Constants
GAME_TABLE = dynamodb.Table(os.environ.get('GAME_TABLE', 'twenty_questions_games'))
LLAMA_MODEL_ID = "meta.llama3-8b-instruct-v1:0"

class Answer(str, Enum):
    YES = 'yes'
    NO = 'no'
    UNKNOWN = 'unknown'

class GameState(BaseModel):
    session_id: str
    word_of_day: str
    start_time: str
    current_question: int = 0
    previous_questions: List[str] = []
    previous_answers: Dict[str, Answer] = {}
    guesses_made: List[str] = []
    is_complete: bool = False
    final_guess: Optional[str] = None

def analyze_question_with_bedrock(question: str, word: str) -> Answer:
    formatted_prompt = f"""
<|begin_of_text|><|start_header_id|>user<|end_header_id|>
The word is "{word}". Based on this, answer the following yes/no question 
with only "yes", "no", or "unknown": {question}
<|eot_id|>
<|start_header_id|>assistant<|end_header_id|>
"""

    body = json.dumps({
        "prompt": formatted_prompt,
        "max_gen_len": 10,
        "temperature": 0.1,
    })

    try:
        response = bedrock_runtime.invoke_model(
            modelId=LLAMA_MODEL_ID,
            body=body
        )
        response_body = json.loads(response['body'].read())
        answer = response_body['generation'].strip().lower()
        
        if answer in ['yes', 'no']:
            return Answer(answer)
        return Answer.UNKNOWN
    except Exception as e:
        print(f"Error invoking Bedrock: {e}")
        return Answer.UNKNOWN

def deserialize_game_state(item: dict) -> GameState:
    return GameState(**item)

def serialize_game_state(game_state: GameState) -> dict:
    game_dict = game_state.dict()
    if isinstance(game_dict['start_time'], datetime):
        game_dict['start_time'] = game_dict['start_time'].isoformat()
    return game_dict

def ask_question(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    try:
        # Validate input
        if not event.get('body'):
            return {
                'statusCode': 400,
                'body': json.dumps({'error': 'Missing request body'})
            }

        # Safely parse JSON
        try:
            body = json.loads(event['body'])
        except json.JSONDecodeError:
            return {
                'statusCode': 400,
                'body': json.dumps({'error': 'Invalid JSON in request body'})
            }

        # Validate required fields
        if not all(k in body for k in ['session_id', 'question']):
            return {
                'statusCode': 400,
                'body': json.dumps({'error': 'Missing required fields'})
            }

        session_id = body['session_id']
        question = body['question']

        # Get game state
        response = GAME_TABLE.get_item(Key={'session_id': session_id})
        if 'Item' not in response:
            return {
                'statusCode': 404,
                'body': json.dumps({'error': 'Game not found'})
            }
        
        game_state = deserialize_game_state(response['Item'])
        
        if game_state.is_complete:
            return {
                'statusCode': 400,
                'body': json.dumps({'error': 'Game is already complete'})
            }
        
        if game_state.current_question >= 20:
            return {
                'statusCode': 400,
                'body': json.dumps({'error': 'No more questions allowed'})
            }
        
        answer = analyze_question_with_bedrock(question, game_state.word_of_day)
        
        game_state.previous_questions.append(question)
        game_state.previous_answers[f"question_{game_state.current_question}"] = answer
        game_state.current_question += 1
        
        # Update DynamoDB
        GAME_TABLE.update_item(
            Key={'session_id': session_id},
            UpdateExpression="SET previous_questions = :q, previous_answers = :a, current_question = :c",
            ExpressionAttributeValues={
                ':q': game_state.previous_questions,
                ':a': game_state.previous_answers,
                ':c': game_state.current_question
            }
        )
        
        return {
            'statusCode': 200,
            'body': json.dumps({
                'answer': answer,
                'questions_remaining': 20 - game_state.current_question,
                'question_number': game_state.current_question
            })
        }
    except Exception as e:
        # Log the full error for debugging
        print(f"Error processing request: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({'error': 'Internal server error'})
        }