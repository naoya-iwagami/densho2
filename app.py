import os  
import json  
import threading  
import datetime  
import uuid  
  
from flask import Flask, request, render_template, redirect, url_for, session, flash, jsonify, Response  
from flask_session import Session  
from azure.cosmos import CosmosClient  
from openai import AzureOpenAI  
import markdown2  
  
app = Flask(__name__)  
app.secret_key = os.getenv('FLASK_SECRET_KEY', 'your-default-secret-key')  
app.config['SESSION_TYPE'] = 'filesystem'  
app.config['SESSION_FILE_DIR'] = os.path.join(os.getcwd(), 'flask_session')  
app.config['SESSION_PERMANENT'] = False  
Session(app)  
  
client = AzureOpenAI(  
    api_key=os.getenv("AZURE_OPENAI_KEY"),  
    api_version=os.getenv("AZURE_OPENAI_API_VERSION"),  
    azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT")  
)  
cosmos_endpoint = os.getenv("AZURE_COSMOS_ENDPOINT")  
cosmos_key = os.getenv("AZURE_COSMOS_KEY")  
database_name = 'chatdb'  
container_name = 'densho2'  
cosmos_client = CosmosClient(cosmos_endpoint, credential=cosmos_key)  
database = cosmos_client.get_database_client(database_name)  
container = database.get_container_client(container_name)  
lock = threading.Lock()  
  
TOPICS = [  
    {  
        "id": "customer",  
        "name": "顧客対応・クレーム対応の実例とノウハウ",  
        "description": "過去の顧客トラブルの対応や基準、社内外連絡・エスカレーションなどの実例やノウハウ。",  
        "questions": [  
            "どのような重大クレームが過去に発生し、どんな対応で顧客の納得を得ましたか？",  
            "顧客との取り決め事項はどのように管理・共有していますか？",  
            "初めての故障モードが発生した場合、顧客ごとに出荷可否をどう判断していますか？"  
        ]  
    },  
    {  
        "id": "standard",  
        "name": "規格・基準の設定経緯と運用",  
        "description": "規格や基準値の設定理由や運用、重要な見直しポイント、承認プロセスなど。",  
        "questions": [  
            "ある規格や検査項目は、どんな背景や根拠で設定されたのですか？",  
            "規格を緩和・廃止する際、何を基準に「ここは絶対に残す」と判断していますか？",  
            "規格の新設・変更は、どの部署・誰が最終承認していますか？"  
        ]  
    },  
    {  
        "id": "quality",  
        "name": "品質保証体制・社内判断プロセス",  
        "description": "品質保証体制の変遷や判断プロセス・部門間連携・再発防止策の運用例。",  
        "questions": [  
            "品質保証体制はどのように立ち上げられ、どんな経緯で変遷してきましたか？",  
            "クレーム発生時、社内でどのような連携や判断プロセスを経て対応していますか？",  
            "再発防止策はどのレベル（現場・設計・工程管理など）で決め、どのように運用していますか？"  
        ]  
    },  
    {  
        "id": "case",  
        "name": "過去事例・ナレッジの記録・活用",  
        "description": "品質トラブルやクレーム等の事例集・記録方法・FAQ化などナレッジ共有の工夫。",  
        "questions": [  
            "過去のクレームや品質トラブルは、どのように記録・整理されていますか？",  
            "事例集には、どんな項目（原因・確認・直し方・成果など）が載っていると現場で役立ちますか？",  
            "重大度や発生現象ごとに分類したトラブル事例集はありますか？"  
        ]  
    },  
    {  
        "id": "decision",  
        "name": "判断・意思決定の根拠・本音",  
        "description": "判断に迷った場面や実感、本音・説明の根拠・承認プロセスのポイント。",  
        "questions": [  
            "過去の重大な判断で、今ならこうしたかった・本当はやりたかったことはありますか？",  
            "規格や基準を決めるとき、どんな根拠やロジックで説明しましたか？",  
            "クレーム対応や規格設定で、特に苦労した・迷った場面とその乗り越え方を教えてください。"  
        ]  
    },  
    {  
        "id": "share",  
        "name": "情報共有・伝承時の困りごと・工夫",  
        "description": "資料の散逸や記憶への依存・形式やFAQ可・情報収集や深掘りの工夫。",  
        "questions": [  
            "過去の判断や経緯が記録化されていない場合、どのように情報を集めていますか？",  
            "伝承の際、どのようなフォーマットや整理方法が現場で活用しやすいですか？",  
            "口頭伝承や記憶頼みになっている情報を、どう一覧化・FAQ化するのが望ましいですか？"  
        ]  
    }  
]  
  
def get_authenticated_user():  
    if "user_id" in session and "user_name" in session:  
        return session["user_id"]  
    session["user_id"] = "anonymous"  
    session["user_name"] = "anonymous"  
    return session["user_id"]  
  
def save_chat_history():  
    with lock:  
        try:  
            user_id = get_authenticated_user()  
            question_histories = session.get("question_histories", {})  
            for qkey, h in question_histories.items():  
                if not h.get("messages") or len([m for m in h["messages"] if m["role"] == "user"]) == 0:  
                    continue  
                topic_id, q_index = qkey.split('_')  
                q_index = int(q_index)  
                topic = next(t for t in TOPICS if t["id"] == topic_id)  
                item = {  
                    'id': f"{user_id}_{topic_id}_{q_index}",  
                    'user_id': user_id,  
                    'user_name': session.get("user_name", "anonymous"),  
                    'topic_id': topic_id,  
                    'topic_name': topic['name'],  
                    'question_index': q_index,  
                    'question_text': topic["questions"][q_index],  
                    'messages': h["messages"],  
                    'session_id': h.get("session_id"),  
                    'timestamp': datetime.datetime.utcnow().isoformat()  
                }  
                container.upsert_item(item)  
        except Exception as e:  
            print(f"チャット履歴保存エラー: {e}")  
  
def load_chat_history():  
    with lock:  
        user_id = get_authenticated_user()  
        question_histories = {}  
        try:  
            query = "SELECT * FROM c WHERE c.user_id = @user_id ORDER BY c.timestamp DESC"  
            parameters = [{"name": "@user_id", "value": user_id}]  
            items = container.query_items(query=query, parameters=parameters, enable_cross_partition_query=True)  
            for item in items:  
                topic_id = item.get('topic_id')  
                q_index = item.get('question_index')  
                if topic_id is None or q_index is None or not item.get('messages'):  
                    continue  
                qkey = f"{topic_id}_{q_index}"  
                question_histories[qkey] = {  
                    "session_id": item.get('session_id', str(uuid.uuid4())),  
                    "messages": item['messages']  
                }  
        except Exception as e:  
            print(f"チャット履歴読み込みエラー: {e}")  
        return question_histories  
  
@app.route('/', methods=['GET', 'POST'])  
def index():  
    get_authenticated_user()  
    if "question_histories" not in session:  
        session["question_histories"] = load_chat_history() or {}  
    if "selected_topic" not in session:  
        session["selected_topic"] = TOPICS[0]['id']  
    if "selected_question" not in session:  
        session["selected_question"] = 0  
  
    if request.method == 'POST':  
        if 'select_question' in request.form:  
            t_id = request.form.get("select_topic")  
            q_index = int(request.form.get("select_question"))  
            session["selected_topic"] = t_id  
            session["selected_question"] = q_index  
            qkey = f"{t_id}_{q_index}"  
            hists = session.get("question_histories", {})  
            if qkey not in hists:  
                question = next(t for t in TOPICS if t['id'] == t_id)["questions"][q_index]  
                hists[qkey] = {  
                    "session_id": str(uuid.uuid4()),  
                    "messages": [ {"role": "assistant", "content": question, "type": "text"} ]  
                }  
                session["question_histories"] = hists  
            session.modified = True  
            return redirect(url_for('index'))  
  
    t_id = session["selected_topic"]  
    q_idx = session["selected_question"]  
    qkey = f"{t_id}_{q_idx}"  
    chat_history = session.get("question_histories", {}).get(qkey, {}).get("messages", [])  
    topic = next(t for t in TOPICS if t["id"] == t_id)  
  
    # 回答進捗（全体・各質問）  
    histories = session.get("question_histories", {})  
    answer_status = {}  
    total_count = 0  
    total_questions = 0  
    for t in TOPICS:  
        tid = t["id"]  
        answer_status_tid = []  
        for q_i, q in enumerate(t["questions"]):  
            qkey = f"{tid}_{q_i}"  
            msgs = histories.get(qkey, {}).get("messages", [])  
            count = len([m for m in msgs if m.get("role") == "user"])  
            pct = min(100, int(round(count * 10)))  
            answer_status_tid.append({  
                "count": count,  
                "percent": pct  
            })  
            # 全体進捗は1問10回で100%なので、10回を超過しても10回分のみ計上  
            total_count += min(count, 10)  
            total_questions += 1  
        answer_status[tid] = answer_status_tid  
  
    # 全体進捗率を計算（100%超えない）  
    total_progress = 0  
    if total_questions > 0:  
        total_progress = int(round((total_count / (total_questions * 10)) * 100))  
        total_progress = min(100, total_progress)  
  
    return render_template(  
        'index.html',  
        topics=TOPICS,  
        selected_topic=t_id,  
        selected_question=q_idx,  
        chat_history=chat_history,  
        topic_desc=topic["description"],  
        topic_full=topic,  
        questions=topic["questions"],  
        session=session,  
        answer_status=answer_status,  
        total_progress=total_progress,  
    )  
  
@app.route('/send_message', methods=['POST'])  
def send_message():  
    data = request.get_json()  
    prompt = data.get('prompt', '').strip()  
    if not prompt:  
        return json.dumps({'response': ''}), 400, {'Content-Type': 'application/json'}  
  
    t_id = session.get("selected_topic")  
    q_idx = session.get("selected_question")  
    qkey = f"{t_id}_{q_idx}"  
    hists = session.get("question_histories", {})  
    if qkey not in hists:  
        return json.dumps({'response': '開始されていないセッションです'}), 400, {'Content-Type': 'application/json'}  
    messages = hists[qkey]["messages"]  
    messages.append({"role": "user", "content": prompt, "type": "text"})  
  
    # サブ質問深掘り（GPTへ投げる）  
    topic = next(t for t in TOPICS if t["id"] == t_id)  
    question = topic["questions"][q_idx]  
    try:  
        system_msg = (  
            f"あなたは伝承ヒアリングAIです。\n"  
            f"カテゴリ：{topic['name']}\n概要：{topic['description']}\n"  
            f"質問：{question}\n"  
            "ユーザー回答を深掘りする追加質問を考え、実例や判断根拠・裏話・本音などを引き出してください。"  
        )  
        messages_list = [{"role": "system", "content": system_msg}]  
        messages_list.extend(messages[-10:])  # 直近10  
        model_name = "gpt-4.1"  
        response_obj = client.chat.completions.create(  
            model=model_name, messages=messages_list  
        )  
        assistant_response = response_obj.choices[0].message.content  
        assistant_response_html = markdown2.markdown(  
            assistant_response,  
            extras=["tables", "fenced-code-blocks", "code-friendly", "break-on-newline", "cuddled-lists"]  
        )  
        messages.append({"role": "assistant", "content": assistant_response_html, "type": "html"})  
    except Exception as e:  
        return json.dumps({'response': f"エラーが発生しました: {e}"}), 500, {'Content-Type': 'application/json'}  
  
    hists[qkey]["messages"] = messages  
    session["question_histories"] = hists  
    save_chat_history()  
    session.modified = True  
    resp = messages[-1]  
    return json.dumps({'response': resp["content"]}), 200, {'Content-Type': 'application/json'}  
  
if __name__ == '__main__':  
    app.run(debug=True, host='0.0.0.0')  