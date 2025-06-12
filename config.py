import os
class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'a_very_secret_key_that_should_be_in_env'
    MONGO_URI = os.environ.get('MONGO_URI')
   