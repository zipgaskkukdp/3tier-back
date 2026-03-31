from flask import Flask, jsonify, request, session
import pymysql
import boto3
from datetime import datetime, timedelta
import os
from dotenv import load_dotenv
from flask_cors import CORS# [추가] dotenv 라이브러리 임포트

# [추가] .env 파일의 내용을 환경변수로 불러오기
load_dotenv() 

# RDS 연결 설정 (직접 적힌 값들 제거)
db_config = {
    'host': os.getenv('DB_HOST'),
    'user': os.getenv('DB_USER'),
    'password': os.getenv('DB_PASS'),
#    'db': 'board_db',
    'charset': 'utf8mb4',
    'cursorclass': pymysql.cursors.DictCursor
}

app = Flask(__name__)
CORS(app, supports_credentials=True)
def init_db():
    
    # DB 이름 없이 연결 (DB를 생성해야 하므로)
    conn = pymysql.connect(
        host=os.getenv('DB_HOST'),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASS'),
        charset='utf8mb4'
    )
    try:
        with conn.cursor() as cursor:
            # 1. 데이터베이스 생성
            cursor.execute("CREATE DATABASE IF NOT EXISTS board_db;")
            
            # 2. 생성한 DB 선택
            cursor.execute("USE board_db;")

            # 3. 사용자 테이블 생성
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    username VARCHAR(50) UNIQUE NOT NULL,
                    password VARCHAR(255) NOT NULL,
                    withdraw_password VARCHAR(255) NOT NULL
                );
            """)

            # 4. 게시글 테이블 생성
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS posts (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    title VARCHAR(255) NOT NULL,
                    content TEXT,
                    author VARCHAR(50),
                    image_url VARCHAR(500),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
        conn.commit()
        print("✅ 데이터베이스 및 테이블 초기화 완료!")
    except Exception as e:
        print(f"❌ DB 초기화 중 에러 발생: {e}")
    finally:
        conn.close()

# 앱 시작 시 DB 초기화 실행
init_db()

db_config['db'] = 'board_db'

# 기존 API 함수들에서 사용할 때는 board_db를 명시하도록 db_config 업데이트
# [수정] 직접 입력 대신 os.getenv 사용
app.secret_key = os.getenv('SECRET_KEY') 
app.permanent_session_lifetime = timedelta(days=7)


# AWS S3 설정 (버킷명과 리전은 보안값이 아니므로 유지해도 되지만, 키값은 제거)
S3_BUCKET = 'niha5ma-storage-904053119728-final'
S3_ACCESS_KEY = os.getenv('S3_KEY')
S3_SECRET_KEY = os.getenv('S3_SECRET')
S3_REGION = 'ap-northeast-2'

s3 = boto3.client(
    's3',
    aws_access_key_id=S3_ACCESS_KEY,
    aws_secret_access_key=S3_SECRET_KEY,
    region_name=S3_REGION
)

# 1. 회원가입 API (2차 비밀번호 포함)
@app.route('/api/register', methods=['POST'])
def register():
    data = request.json
    username = data.get('username')
    password = data.get('password')
    withdraw_pw = data.get('withdraw_password') # 프론트에서 보낸 2차 비번

    # 필수 데이터 확인
    if not username or not password or not withdraw_pw:
        return jsonify({"error": "모든 필드를 입력해주세요."}), 400

    conn = pymysql.connect(**db_config)
    try:
        with conn.cursor() as cursor:
            # withdraw_password 컬럼에 값을 함께 저장합니다.
            sql = "INSERT INTO users (username, password, withdraw_password) VALUES (%s, %s, %s)"
            cursor.execute(sql, (username, password, withdraw_pw))
        conn.commit()
        return jsonify({"message": "success"}), 201
    except pymysql.err.IntegrityError:
        return jsonify({"error": "exists"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

# 2. 로그인 API
@app.route('/api/login', methods=['POST'])
def login():
    data = request.json
    conn = pymysql.connect(**db_config)
    try:
        with conn.cursor() as cursor:
            sql = "SELECT username FROM users WHERE username = %s AND password = %s"
            cursor.execute(sql, (data['username'], data['password']))
            user = cursor.fetchone()
            if user:
                session.permanent = True
                session['username'] = user['username']
                return jsonify({"username": user['username']})
            return jsonify({"error": "fail"}), 401
    finally:
        conn.close()

# 3. 로그아웃 API
@app.route('/api/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({"message": "success"})

# 4. 게시글 목록 조회 (GET)
@app.route('/api/posts', methods=['GET'])
def get_posts():
    conn = pymysql.connect(**db_config)
    try:
        with conn.cursor() as cursor:
            # [여기 수정!] author를 반드시 추가해야 목록에 이름이 나옵니다.
            cursor.execute("SELECT id, title, author, created_at FROM posts ORDER BY id DESC")
            result = cursor.fetchall()
            return jsonify(result)
    finally:
        conn.close()


# 5. 게시글 작성 (POST + 이미지 업로드)
@app.route('/api/posts', methods=['POST'])
def create_post():
    if 'username' not in session:
        return jsonify({"error": "Unauthorized"}), 401
    
    # 이미지가 포함된 경우 request.form과 request.files를 사용합니다.
    title = request.form.get('title')
    content = request.form.get('content')
    file = request.files.get('image')
    
    image_url = None
    if file:
        try:
            # S3 업로드 경로 및 파일명 생성
            filename = f"uploads/{datetime.now().strftime('%Y%m%d%H%M%S')}_{file.filename}"
            s3.upload_fileobj(
                file, 
                S3_BUCKET, 
                filename, 
                ExtraArgs={'ACL': 'public-read', 'ContentType': file.content_type}
            )
            image_url = f"https://{S3_BUCKET}.s3.{S3_REGION}.amazonaws.com/{filename}"
        except Exception as e:
            print(f"S3 Upload Error: {e}")
            return jsonify({"error": "S3 upload failed"}), 500

    conn = pymysql.connect(**db_config)
    try:
        with conn.cursor() as cursor:
            # image_url 컬럼이 DB에 있어야 합니다.
            sql = "INSERT INTO posts (title, content, author, image_url) VALUES (%s, %s, %s, %s)"
            cursor.execute(sql, (title, content, session['username'], image_url))
        conn.commit()
        return jsonify({"message": "success"}), 201
    finally:
        conn.close()

# 6. 특정 게시글 상세 조회 (GET)
@app.route('/api/posts/<int:post_id>', methods=['GET'])
def get_post(post_id):
    conn = pymysql.connect(**db_config)
    try:
        with conn.cursor() as cursor:
            # author 컬럼이 포함되도록 전체(*) 조회
            sql = "SELECT * FROM posts WHERE id = %s"
            cursor.execute(sql, (post_id,))
            result = cursor.fetchone()
            return jsonify(result) if result else (jsonify({"error": "Not Found"}), 404)
    finally:
        conn.close()

# 7. 게시글 삭제 (DELETE)
@app.route('/api/posts/<int:post_id>', methods=['DELETE'])
def delete_post(post_id):
    if 'username' not in session:
        return jsonify({"error": "Unauthorized"}), 401

    current_user = session['username']
    conn = pymysql.connect(**db_config)
    try:
        with conn.cursor() as cursor:
            # [검증] 삭제 전, DB에 저장된 작성자가 누구인지 확인
            check_sql = "SELECT author FROM posts WHERE id = %s"
            cursor.execute(check_sql, (post_id,))
            post = cursor.fetchone()

            if not post:
                return jsonify({"error": "Post not found"}), 404

            # [핵심] 로그인한 유저와 작성자가 다르면 삭제 거부
            if post['author'] != current_user:
                return jsonify({"error": "본인 글만 삭제할 수 있습니다."}), 403

            # 본인 확인 완료 시 삭제 실행
            sql = "DELETE FROM posts WHERE id = %s"
            cursor.execute(sql, (post_id,))

        conn.commit()
        return jsonify({"message": "success"}), 200
    finally:
        conn.close()

# 8. 회원 탈퇴 (DELETE)
@app.route('/api/withdraw', methods=['DELETE'])
def withdraw():
    if 'username' not in session:
        return jsonify({"error": "로그인이 필요합니다."}), 401
    
    # [중요] 사용자가 보낸 JSON 데이터를 가져옵니다.
    data = request.get_json() 
    if not data:
        return jsonify({"error": "데이터가 전송되지 않았습니다."}), 400
        
    input_pw = data.get('withdraw_password')
    current_user = session['username']
    
    conn = pymysql.connect(**db_config)
    try:
        with conn.cursor() as cursor:
            # 1. DB에서 해당 사용자의 2차 비번 가져오기
            cursor.execute("SELECT withdraw_password FROM users WHERE username = %s", (current_user,))
            user = cursor.fetchone()
            
            # 2. 비번 검증 (DB에 저장된 값과 입력값 비교)
            if not user or str(user['withdraw_password']) != str(input_pw):
                return jsonify({"error": "2차 비밀번호가 일치하지 않습니다."}), 400
            
            # 3. 일치하면 삭제
            cursor.execute("DELETE FROM users WHERE username = %s", (current_user,))
        
        conn.commit()
        session.clear() # 세션 파기 (로그아웃 처리)
        return jsonify({"message": "회원 탈퇴가 완료되었습니다."})
    except Exception as e:
        print(f"Error: {e}") # 터미널에 에러 내용을 찍어줍니다.
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
