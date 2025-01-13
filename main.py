import boto3
import random
import json
import os
from datetime import datetime, timedelta, date
from enum import Enum
from typing import Dict, List, Optional
from pydantic import BaseModel

# Models
LLAMA_MODEL_ID = "meta.llama3-8b-instruct-v1:0"

class GameStatus(BaseModel):
    questions_asked: int
    questions_remaining: int
    guesses_made: int
    is_complete: bool

class Answer(str, Enum):
    YES = "yes"
    NO = "no"
    UNKNOWN = "unknown"

class GameState(BaseModel):
    session_id: str
    current_question: int = 0
    previous_answers: Dict[str, str] = {}
    previous_questions: List[str] = []
    is_complete: bool = False
    final_guess: Optional[str] = None
    word_of_day: str
    start_time: str
    guesses_made: List[str] = []

    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }

# Initialize AWS clients
bedrock_runtime = boto3.client(
    service_name='bedrock-runtime',
    region_name='us-east-1'
)

s3 = boto3.client('s3')
dynamodb = boto3.resource('dynamodb')

# Constants
WORD_LIST_BUCKET = os.environ.get('WORD_LIST_BUCKET')
GAME_TABLE = dynamodb.Table(os.environ.get('GAME_TABLE', 'twenty_questions_games'))

def get_yesterday_word():
    # Get yesterday's date
    try:
        response = s3.get_object(
            Bucket=WORD_LIST_BUCKET,
            Key='words.json'
        )
        words_data = json.loads(response['Body'].read())
        yesterday = datetime.now().astimezone() - timedelta(days=1)
        yesterday_str = yesterday.date().isoformat()
        
        # Check if yesterday's date exists in daily_words
        if yesterday_str in words_data.get('daily_words', {}):
            return words_data['daily_words'][yesterday_str]
        
        return None  # or return '' depending on your preference
        
    except Exception as e:
        print(f"Error fetching word of the day from bucket: {e}")
        return None  # or return ''


    
def get_word_of_day():
    try:
        response = s3.get_object(
            Bucket=WORD_LIST_BUCKET,
            Key='words.json'
        )
        words_data = json.loads(response['Body'].read())
        today = datetime.now().astimezone().date().isoformat()
        
        # First check if there's a specific word for today in daily_words
        if today in words_data.get('daily_words', {}):
            return words_data['daily_words'][today]
        
        # If no specific word for today, choose randomly from default_words
        return random.choice(words_data['default_words'])
        
    except Exception as e:
        print(f"Error fetching word of the day from bucket: {e}")
        # Fallback default words if there's an error
        default_words = ["chicken", "computer", "elephant", "book", "piano"]
        return random.choice(default_words)
    
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

def lambda_handler(event, context):
    try:
        # Get HTTP method and path from the event
        http_method = event['requestContext']['http']['method']
        path = event['requestContext']['http']['path']
        
        # Parse request body if it exists
        body = {}
        if 'body' in event:
            body = json.loads(event['body'])

        # Route the request based on path and method
        if http_method == 'POST':
            if path == '/start-game':
                session_id = f"game_{datetime.now().timestamp()}"
                word_of_day = get_word_of_day()
                
                game_state = GameState(
                    session_id=session_id,
                    word_of_day=word_of_day,
                    start_time=datetime.now().isoformat()
                )
                
                GAME_TABLE.put_item(Item=serialize_game_state(game_state))
                yesterday_word = get_yesterday_word()
                
                return {
                    'statusCode': 200,
                    'body': json.dumps({
                        'session_id': session_id,
                        'message': 'Welcome to 20 Questions! Think of your questions carefully...',
                        'yesterdays_word': yesterday_word
                    })
                }
            
            elif path.startswith('/ask-question/'):
                session_id = path.split('/')[-1]
                question = body.get('question')
                
                response = GAME_TABLE.get_item(Key={'session_id': session_id})
                if 'Item' not in response:
                    return {
                        'statusCode': 404,
                        'body': json.dumps({'detail': 'Game not found'})
                    }
                
                game_state = deserialize_game_state(response['Item'])
                
                if game_state.is_complete:
                    return {
                        'statusCode': 400,
                        'body': json.dumps({'detail': 'Game is already complete'})
                    }
                
                if game_state.current_question >= 20:
                    return {
                        'statusCode': 400,
                        'body': json.dumps({'detail': 'No more questions allowed'})
                    }
                
                answer = analyze_question_with_bedrock(question, game_state.word_of_day)
                
                game_state.previous_questions.append(question)
                game_state.previous_answers[f"question_{game_state.current_question}"] = answer
                game_state.current_question += 1
                
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
            
            elif path.startswith('/guess/'):
                session_id = path.split('/')[-1]
                guess = body.get('guess')
                
                response = GAME_TABLE.get_item(Key={'session_id': session_id})
                if 'Item' not in response:
                    return {
                        'statusCode': 404,
                        'body': json.dumps({'detail': 'Game not found'})
                    }
                
                game_state = deserialize_game_state(response['Item'])
                
                # Check if game is already complete
                if game_state.is_complete:
                    return {
                        'statusCode': 400,
                        'body': json.dumps({'detail': 'Game is already complete'})
                    }
                
                game_state.guesses_made.append(guess)
                is_correct = guess.lower() == game_state.word_of_day.lower()
                
                # Check if correct or reached max guesses (2)
                if is_correct or len(game_state.guesses_made) >= 2:
                    game_state.is_complete = True
                    game_state.final_guess = guess
                    
                    # Update DynamoDB with all the changes
                    GAME_TABLE.update_item(
                        Key={'session_id': session_id},
                        UpdateExpression="SET is_complete = :ic, final_guess = :fg, guesses_made = :gm",
                        ExpressionAttributeValues={
                            ':ic': True,
                            ':fg': guess,
                            ':gm': game_state.guesses_made
                        }
                    )
                else:
                    # Update DynamoDB with just the guesses made if game is not complete
                    GAME_TABLE.update_item(
                        Key={'session_id': session_id},
                        UpdateExpression="SET guesses_made = :gm",
                        ExpressionAttributeValues={
                            ':gm': game_state.guesses_made
                        }
                    )
                
                return {
                    'statusCode': 200,
                    'body': json.dumps({
                        'result': 'correct' if is_correct else 'incorrect',
                        'game_over': game_state.is_complete,
                        'guesses_remaining': 2 - len(game_state.guesses_made),
                        'message': (
                            'Congratulations! You guessed correctly!' if is_correct
                            else ('Game Over! Tomorrow, we will share the word!' if game_state.is_complete
                                else f'Incorrect. You have {2 - len(game_state.guesses_made)} guess(es) remaining.')
                        )
                    })
                }



        
        return {
            'statusCode': 404,
            'body': json.dumps({'detail': 'Route not found'})
        }
    
    except Exception as e:
        return {
            'statusCode': 500,
            'body': json.dumps({'detail': f'Internal server error: {str(e)}'})
        }
