from authlib.jose import jwt
from dailyfresh.settings import SECRET_KEY
import json
import re
import requests
from flask import Flask


app = Flask(__name__)

@app.route('/')
def index():
    return 'hello flask!!'

app.run()
