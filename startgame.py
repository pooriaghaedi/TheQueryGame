from datetime import datetime, timedelta
import json, os
import random
import boto3
from enum import Enum
from pydantic import BaseModel
from typing import Dict, List, Optional

# AWS service clients
s3 = boto3.client('s3')
bedrock_runtime = boto3.client('bedrock-runtime')
dynamodb = boto3.resource('dynamodb')

# Constants
WORD_LIST_BUCKET = os.environ.get('WORD_LIST_BUCKET')
GAME_TABLE = dynamodb.Table(os.environ.get('GAME_TABLE', 'twenty_questions_games'))

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
        
        if today in words_data.get('daily_words', {}):
            return words_data['daily_words'][today]
        
        return random.choice(words_data['default_words'])
        
    except Exception as e:
        print(f"Error fetching word of the day from bucket: {e}")
        default_words = ["chicken", "computer", "elephant", "book", "piano"]
        return random.choice(default_words)

def deserialize_game_state(item: dict) -> GameState:
    return GameState(**item)

def serialize_game_state(game_state: GameState) -> dict:
    game_dict = game_state.dict()
    if isinstance(game_dict['start_time'], datetime):
        game_dict['start_time'] = game_dict['start_time'].isoformat()
    return game_dict

# API Endpoints
def start_game(event, context):
    try:
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
    except Exception as e:
        return {
            'statusCode': 500,
            'body': json.dumps({'error': f'Internal server error: {str(e)}'})
        }