from migration.base import engine
from sqlmodel import Session, select
from migration.models import ChatSession, ChatMessage, ArticleSource

with Session(engine) as session:
    sessions = session.exec(select(ChatSession)).all()
    messages = session.exec(select(ChatMessage).where(ChatMessage.role == 'bot')).all()
    sources = session.exec(select(ArticleSource)).all()
    
    print('\n--- DB STATUS ---')
    print(f'Total Chat Sessions: {len(sessions)}')
    print(f'Total Generated Articles (Bot Messages): {len(messages)}')
    print(f'Total Article Sources: {len(sources)}')
    
    if sources:
        print('\nLast 3 Sources:')
        for s in sources[-3:]:
            print(f'- {s.judul[:40]} ({s.sumber}) -> {s.url}')
