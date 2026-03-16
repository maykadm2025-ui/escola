from flask import Flask, render_template

app = Flask(__name__)

@app.route("/")
def index():
    # Credenciais e senha – substitua pelos valores desejados ou use variáveis de ambiente
    supabase_url = "https://epbfgvhjrcfjewwmptjj.supabase.co"
    supabase_key = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImVwYmZndmhqcmNmamV3d21wdGpqIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzMxNzk4NjgsImV4cCI6MjA4ODc1NTg2OH0.50JJJgmtSAFCA6ivFyzFW1EiPx2hsxoF5Rl4BvOaiwQ"
    password = "0009"
    return render_template("index.html", supabase_url=supabase_url, supabase_key=supabase_key, password=password)

if __name__ == "__main__":
    app.run(debug=True)