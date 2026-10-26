from flask import Flask, jsonify, request

from agents.milestone_agent import generate_milestones
from agents.negotiation_agent import check_negotiation
from agents.verification_agent import verify_submission

app = Flask(__name__)


@app.post('/ai/generate-milestones')
def api_generate_milestones():
    d = request.get_json(force=True)
    return jsonify(generate_milestones(d.get('description'), d.get('budget'), d.get('deadline')))


@app.post('/ai/verify-milestone')
def api_verify_milestone():
    d = request.get_json(force=True)
    return jsonify(verify_submission(d.get('github_link'), d.get('criteria')))


@app.post('/ai/negotiation-check')
def api_negotiation_check():
    d = request.get_json(force=True)
    return jsonify(check_negotiation(d.get('description'), d.get('budget')))


if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5001)
