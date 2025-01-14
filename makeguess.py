import boto3
import random
import json
import os
from datetime import datetime, timedelta, date
from enum import Enum
from typing import Dict, List, Optional, Any
from pydantic import BaseModel


class GameStatus(BaseModel):
    questions_asked: int
    questions_remaining: int
    guesses_made: int
    is_complete: bool


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

s3 = boto3.client('s3')
dynamodb = boto3.resource('dynamodb')

# Constants
WORD_LIST_BUCKET = os.environ.get('WORD_LIST_BUCKET')
GAME_TABLE = dynamodb.Table(os.environ.get('GAME_TABLE', 'twenty_questions_games'))
    
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
    
def deserialize_game_state(item: dict) -> GameState:
    return GameState(**item)

def serialize_game_state(game_state: GameState) -> dict:
    game_dict = game_state.dict()
    if isinstance(game_dict['start_time'], datetime):
        game_dict['start_time'] = game_dict['start_time'].isoformat()
    return game_dict

def make_guess(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
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
        if not all(k in body for k in ['session_id', 'guess']):
            return {
                'statusCode': 400,
                'body': json.dumps({'error': 'Missing required fields'})
            }

        session_id = body['session_id']
        guess = body['guess']

        # Validate guess is not empty
        if not guess or not isinstance(guess, str):
            return {
                'statusCode': 400,
                'body': json.dumps({'error': 'Invalid guess format'})
            }

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
        
        # Process the guess
        game_state.guesses_made.append(guess)
        is_correct = guess.lower() == game_state.word_of_day.lower()
        
        # Update game state if complete or add new guess
        if is_correct or len(game_state.guesses_made) >= 2:
            game_state.is_complete = True
            game_state.final_guess = guess
            
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
            GAME_TABLE.update_item(
                Key={'session_id': session_id},
                UpdateExpression="SET guesses_made = :gm",
                ExpressionAttributeValues={
                    ':gm': game_state.guesses_made
                }
            )
        
        # Prepare response message
        remaining_guesses = 2 - len(game_state.guesses_made)
        message = (
            'Congratulations! You guessed correctly!' if is_correct
            else ('Game Over! Tomorrow, we will share the word!' if game_state.is_complete
                else f'Incorrect. You have {remaining_guesses} guess(es) remaining.')
        )

        return {
            'statusCode': 200,
            'body': json.dumps({
                'result': 'correct' if is_correct else 'incorrect',
                'game_over': game_state.is_complete,
                'guesses_remaining': remaining_guesses,
                'message': message
            })
        }

    except Exception as e:
        # Log the error for debugging
        print(f"Error processing guess: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({'error': 'Internal server error'})
        }