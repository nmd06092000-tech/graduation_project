from flask import Blueprint, render_template

public_bp = Blueprint('public', __name__)


# 1. ログイン選択画面（トップページ）
@public_bp.route('/')
def index():
    return render_template('public/index.html')


# はじめての方へ（使い方ガイド）
@public_bp.route('/guide')
def guide():
    return render_template('public/guide.html')


# よくある質問（FAQ）
@public_bp.route('/faq')
def faq():
    return render_template('public/faq.html')
