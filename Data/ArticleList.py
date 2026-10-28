from flask import Blueprint, render_template, request, abort
from firebase_admin import firestore

article_list_bp = Blueprint('article_list_bp', __name__)

# Articles List
@article_list_bp.route('/education/articles', methods=['GET'])
def list_articles():
    try:
        db = firestore.client()
        search_query = request.args.get('search', '').strip().lower()
        category_filter = request.args.get('category', '').strip()
        articles_ref = db.collection('Articles').order_by('createdAt', direction=firestore.Query.DESCENDING).stream()

        articles = []
        categories = set()
        
        for doc in articles_ref:
            data = doc.to_dict()
            data['id'] = doc.id

            if 'createdAt' in data and data['createdAt'] is not None:
                try:
                    data['createdAt'] = data['createdAt'].datetime
                except AttributeError:
                    pass

            if 'category' in data and data['category']:
                categories.add(data['category'].strip())
                
            match_search = not search_query or search_query in data.get('title', '').lower() or search_query in data.get('content', '').lower()
            match_category = not category_filter or category_filter.lower() == data.get('category', '').lower()
            
            if match_search and match_category:
                articles.append(data)

        featured_article = articles[0] if articles else None
        remaining_articles = articles[1:] if len(articles) > 1 else []

        return render_template(
            'ArticleList.html',
            featured_article=featured_article,
            articles=remaining_articles,
            categories=sorted(list(categories)),
            active_category=category_filter,
            search_query=search_query
        )
        
    except Exception as e:
        print(f"Error serving article list: {str(e)}")
        abort(500)