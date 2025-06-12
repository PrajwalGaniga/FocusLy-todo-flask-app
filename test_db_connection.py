import os
from dotenv import load_dotenv
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure, OperationFailure

load_dotenv() # Load your .env file

mongo_uri = os.environ.get('MONGO_URI')

print(f"Attempting direct connection with URI: {mongo_uri}")

client = None # Initialize client to None
try:
    # Create a MongoClient instance - this is where the connection attempt happens
    client = MongoClient(mongo_uri)
    
    # The ismaster command is cheap and does not require auth.
    # This will raise an exception if connection fails.
    client.admin.command('ismaster') 
    
    print(f"SUCCESS: Directly connected to MongoDB Atlas!")
    print("You are connected to the Atlas cluster.")
    print("Collections in 'taskmanager' database (if exists):")
    try:
        db_taskmanager = client.get_database("taskmanager")
        print(db_taskmanager.list_collection_names())
    except OperationFailure as e:
        print(f"Could not list collections (permission issue?): {e}")
    except Exception as e:
        print(f"Error accessing taskmanager db: {e}")

except ConnectionFailure as e:
    print(f"ERROR (ConnectionFailure): Could not connect to MongoDB Atlas.")
    print(f"Details: {e}")
except OperationFailure as e:
    print(f"ERROR (OperationFailure - likely auth/permissions): Could not connect to MongoDB Atlas.")
    print(f"Details: {e}")
except Exception as e:
    print(f"ERROR (Other Exception): An unexpected error occurred during connection.")
    print(f"Details: {e}")
finally:
    if client:
        client.close()
        print("Client connection closed.")