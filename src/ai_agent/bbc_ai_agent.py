import os
import sys
import time
import json
import html
import shutil
from datetime import datetime

import feedparser              # Parses BBC's XML RSS feed into Python objects
import ollama                  # Official client to talk to the local Ollama server

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
packer_dir = os.path.join(SCRIPT_DIR, '..')

sys.path.append(packer_dir)
from csv_to_bin_packer import row_to_record, STRUCT_SIZE, category_dict
from daemons.daemon_client_lib import (
    start_daemon,
    start_heartbeat,
    get_seen_urls,
    update_urls,
)


INJECTOR_DIR = os.path.join(SCRIPT_DIR, '../../..', 'injector_src', 'latest')
INJECTOR_PATH = os.path.join(INJECTOR_DIR, 'packed_news.bin')
ARCHIVE_DIR = os.path.join(SCRIPT_DIR, 'archive')
ARCHIVE_BIN = os.path.join(ARCHIVE_DIR, 'master_archive.bin')
STAGING_DIR = os.path.join(SCRIPT_DIR, 'staging_folder')
STAGING_BIN = os.path.join(STAGING_DIR,'staged_news.bin')
MASTER_CATEGORIES_PATH = os.path.join(SCRIPT_DIR, 'json', 'master_categories.json')

CATEGORIZATION_PROMPT = """You are a strict news classification engine.
Given a news headline and summary, return the single best Category ID (integer 0 to 11):
0: General (vague, magazine, unclassifiable)
1: World News (international affairs, diplomacy, wars, global events)
2: Politics (elections, government, policy, congress)
3: Business & Finance (markets, economy, corporate, trade)
4: Technology (software, hardware, AI, cyber, internet)
5: Science (space, physics, research, archaeology)
6: Health (medicine, diseases, wellness, healthcare)
7: Sports (matches, leagues, athletes, Olympics)
8: Entertainment & Arts (film, music, celebrity, culture)
9: Environment (climate, wildlife, weather, nature)
10: Crime & Law (police, trials, legal, prison)
11: Lifestyle (travel, food, style, relationships)

Respond ONLY with a valid JSON object: {"category_id": <int>}"""

SUMMARIZATION_PROMPT = """You are a professional news editor.
Given a news headline and context, write a single concise, factual summary sentence.
STRICT LENGTH RULE:
The summary MUST be at least 100 characters and AT MOST 250 characters.
Do NOT include preamble, intros, or conversational filler.
Respond ONLY with a valid JSON object: {"summary": "<your summary text>"}"""

AD_DETECTION_PROMPT = """You are a strict advertisement and promotional content detector for a BBC news feed.
Your ONLY job is to determine whether an item is an advertisement/promotional widget or a real news article.
HOT KEYWORDS & PATTERNS OF BBC ADS/PROMOS:
- Newsletter Signups: 'Sign up now', 'to your inbox', 'daily briefing', 'subscribe', 'newsletter'
- App Downloads: 'BBC News app', 'download the app', 'install the app', 'available on iOS/Android'
- Media Platform Cross-Promotions: 'BBC iPlayer', 'BBC Sounds', 'watch now on iPlayer', 'listen on BBC Sounds', 'stream now'
- Commercial / Self-Promotion: promotional pitches, events, merchandise, surveys, promotions
RULES:
- If the item is promoting a BBC service, app, show, podcast, or newsletter, it IS AN AD (is_ad: true).
- If the item is reporting on real-world events, politics, crime, science, business, or sports, it is NOT an ad (is_ad: false).
Respond ONLY with a valid JSON object: {"is_ad": True} or {"is_ad": False}"""

BBC_FEED_URL = "http://feeds.bbci.co.uk/news/rss.xml"
AGENT_RETRIES = 5
# 12 Clean Master Categories (ID -> Name)
MASTER_CATEGORIES = {}
WAIT_TIME = 15 * 60



os.makedirs(INJECTOR_DIR, exist_ok=True)
os.makedirs(ARCHIVE_DIR, exist_ok=True)
os.makedirs(STAGING_DIR, exist_ok=True)

def main():
    print('-_- Starting Agent...')
    print('-_- Starting Daemon...')
    # 1. boot up the background socket daemon if it isn't running yet
    start_daemon()
    # 2. start background thread that pings daemon every 60s so it doesn't shut down
    stop_heartbeat = start_heartbeat()
    global MASTER_CATEGORIES
    MASTER_CATEGORIES = load_master_categories()
    print('-_- Agent is running...')
    # 3. run the main news extraction loop
    run_agent()
    # 4. signal the heartbeat thread to wake up and exit cleanly
    stop_heartbeat.set()
    print('-_- Shutdown succesfull.')
    input('Press Enter to exit...')



# main loop: continuously pulls feed, filters ads & duplicates, packs into binary, and stages for cpp
def run_agent():
    feed = []
    cache = {}
    error_count = 0
    total_records_procesed = 0
    while True:
        try:
            
            print('-_- Fetching feed from BBC...')
            feed = fetch_bbc_feed()
            # ask daemon which URLs we already processed in earlier cycles
            cache = get_seen_urls()
            # skip any article whose URL is already in our seen cache
            new_feed = [entry for entry in feed if entry.get('link', '') not in cache]
            urls_proessed = {}
            print('-_- Processing Feed...')
            for entry in new_feed:
                # filter out ads and promos before doing heavy packing or AI work
                if is_ad(entry):
                    print(f'-_- [Skipped] Ad: {entry.get("title", "")}')
                    urls_proessed.update({entry.get('link', 'INVALID') : now})
                    continue
                # pack the clean news item into our 1,188-byte binary struct
                record = pack_record(entry)
                # append to staging buffer (for C++) and master archive (permanent backup)
                stage_and_archive__record(record)
                now = int(time.time())
                urls_proessed.update({entry.get('link', 'INVALID') : now})
                print(f'-_- [Processed] Article: {entry.get("title", "")}')

            # if we processed new articles, send them to the daemon to update the cache
            if urls_proessed:
                total_records_procesed += len(urls_proessed)
                update_urls(urls_proessed)
                print(f'-_- Processed Entries: {len(urls_proessed)}')
            cache = get_seen_urls()
            print('-_- Handing data to DB...')
            # try to atomically hand off staged records to C++ slot if slot is empty
            try_handoff_to_injector()
            error_count = 0
            # wait 15 minutes because BBC updates their RSS feed every 15 mins (source <ttl>)
            print(f'-_- [Waiting] {WAIT_TIME//60} mins for news to refresh...')
            time.sleep(WAIT_TIME)

        except KeyboardInterrupt:
            print('-_- Agent manual kill override engaged.')
            print(f'-_- Total records processed in recent lifecycle: {total_records_procesed}')
            print('-_- shutting down...')
            return 
        except Exception as e:
            print(f'-_- [Warning] An error occured during processing({AGENT_RETRIES - error_count}/{AGENT_RETRIES}): {e}')
            print(f'-_- Trying again in {WAIT_TIME//60} mins...')
            error_count += 1
            if error_count > AGENT_RETRIES:
                print(f'-_- [Shutdown] Encoutered too many consecutive errors({error_count}).')
                print('-_- Shutting down...')
                return
            time.sleep(WAIT_TIME)

        
        
def is_ad_heuristic(entry: dict) -> bool:    
    link = entry.get('link', '').lower()
    title = entry.get('title', '').strip().lower()
    summary = entry.get('summary', '').lower()

    # Check the links, title and summary with hot words detected by ai for bbc feed ads
    if 'at_medium=display_ad' in link:
        return True
       
    if '/iplayer/' in link or '/sounds/' in link or '/programmes/' in link:
        return True
    
    if 'bbc news app' in title or 'sign up now' in title:
        return True
    if 'to your inbox' in summary or 'download the bbc' in summary:
        return True
    return False

# let the ai check if an entry is ad based on specific bbc hot words produced by larger LLM
def is_ad_with_llama(entry: dict, depth: int = AGENT_RETRIES) -> bool:
    if depth <= 0:
        return False
    link = entry.get('link', '')
    title = entry.get('title', '')
    summary = entry.get('summary', '')
    try:
        response = ollama.chat(
            model='llama3.2',
            messages=[
                {'role': 'system', 'content': AD_DETECTION_PROMPT},
                {'role': 'user', 'content': f'Title: {title}\nSummary: {summary}'}
            ],
            format='json',
            options={'temperature': 0.0}
        )
        content = json.loads(response['message']['content'])

        if not isinstance(content.get('is_ad'), bool):
            return is_ad_with_llama(entry, depth - 1)

        return bool(content.get('is_ad', False))
    except Exception as e:
        print(f'-_- [WARNING] Ollama ad detection error ({depth}/{AGENT_RETRIES}): {e}')
        return is_ad_with_llama(entry, depth - 1)

# returns true if either the fast regex check or the AI check flags this item as an ad
def is_ad(entry: dict) -> bool:
    if is_ad_heuristic(entry):
        return True

    if is_ad_with_llama(entry):
        return True

    return False

# appends binary record to both staging file (buffer for C++) and master archive (permanent backup)
def stage_and_archive__record(record: bytes ,staging_path: str = STAGING_BIN, archive_bin: str = ARCHIVE_BIN):
    if not record:
        return
    with open(staging_path, 'ab') as s_file:
        s_file.write(record)

    with open(archive_bin, 'ab') as a_file:
        a_file.write(record)
    
# atomic handoff: if C++ consumed the previous batch, swap staging into latest slot via NTFS rename
def try_handoff_to_injector(inj_bin_path : str = INJECTOR_PATH, staging_bin: str = STAGING_BIN):
    # return if downstream C++ database has not consumed the previous batch yet
    if os.path.exists(inj_bin_path):
        return

    # if file exists in staging bin and has records, move atomically in 0 ms
    if os.path.exists(staging_bin) and os.path.getsize(staging_bin):
        shutil.move(staging_bin, inj_bin_path)
        print(f'-_- Handed latest records to DB from:\n-_- {staging_bin} to {inj_bin_path}')


# converts raw RSS entry dictionary into our exact 1,188-byte binary struct
def pack_record(entry: dict) -> bytes:
    title = entry.get('title', '')
    link = entry.get('link', '')
    
    if not title or not link:
        return b''
    
    published_on = entry.get('published_on', time.time())
    summary = clean_summary(title, entry.get('summary', ''))
    author = 'BBC news'
    category = categorize_with_ollama(title, summary)

    record = {
        'title' : title,
        'article' : summary,
        'section' : category,
        'published_on' : published_on,
        'url' : link,
        'author' : author
    }

    return row_to_record(record)
    

    

# load the 12 master categories and flip { "0": "General" } to { "General": 0 }
def load_master_categories(master_path : str = MASTER_CATEGORIES_PATH):
    if not os.path.exists(master_path):
        return {
            "General" : 0,
            "World News" : 1,
            "Politics" : 2,
            "Business & Finance" : 3,
            "Technology" : 4,
            "Science" : 5,
            "Health" : 6,
            "Sports" : 7,
            "Entertainment & Arts" : 8,
            "Environment" : 9,
            "Crime & Law" : 10,
            "Lifestyle" : 11
            }
    processed_master = {}
    with open(master_path, 'r', encoding='utf-8') as file:
        raw_master =  json.load(file)
        for key, value in raw_master.items():
            processed_master.update({value : int(key)})

    return processed_master

# fetches live BBC feed and parses entries into clean dictionaries
def fetch_bbc_feed(url : str = BBC_FEED_URL) -> list[dict]:
    feed = feedparser.parse(url)

    # check if the feed is ok and has entries, if not return empty list
    if feed.bozo and not feed.entries:
        return []

    articles = []
    # get each entries
    for entry in feed.entries:
        title = entry.get('title', '').strip()
        link = entry.get('link', '').strip()

        # get parsed date struct and convert it to unix timastamp
        if hasattr(entry, 'published_parsed') and entry.published_parsed:
            published_on = int(time.mktime(entry.published_parsed))
        else:
            published_on = int(time.time())

        # get summary
        summary = entry.get('summary', '').strip()

        if not title or not link:
            continue

        articles.append({
            'title' : html.unescape(title),
            'link' : link,
            'published_on' : published_on,
            'summary' : html.unescape(summary)
        })


    return articles

# unescapes HTML entities (&amp;, &quot;) and ensures summary fits between 100 and 255 chars
def clean_summary(title : str, summary : str) -> str:
    if not title or not summary:
        return ''
    cleansed_summary = html.unescape(summary)
    # if summary is too short or too long for our binary buffer, let Ollama rewrite it
    if not (100 <= len(cleansed_summary) <= 255):
            cleansed_summary = summarize_with_ollama(title, cleansed_summary)

    return cleansed_summary

# asks llama3.2 to write a concise 100-250 char summary with recursive retry on invalid JSON
def summarize_with_ollama(title : str, summary : str, depth : int = AGENT_RETRIES) -> str:
    # fallback to simple string slice if retries exhausted
    if depth <= 0:
        return summary[:250] if len(summary) > 250 else summary

    try:
        response = ollama.chat(
            model='llama3.2',
            messages=[
                {'role' : 'system', 'content' : SUMMARIZATION_PROMPT},
                {'role' : 'user', 'content' : f'Title: {title}, Context: {summary}'}
            ],
            format= 'json',
            options= {'temperature' : 0.1}
        )

        content = json.loads(response['message']['content'])
        ai_summary = content.get('summary', '').strip()

        # enforce strict length bounds for binary struct
        if 100 <= len(ai_summary) <= 255:
            return ai_summary

        return summarize_with_ollama(title, summary, depth - 1)

    except Exception as e:
        print(f'-_- [WARNING] Ollama encountered an error during processing...\n({depth}/{AGENT_RETRIES}): {e} ')
        return summarize_with_ollama(title, summary, depth - 1)

# asks llama3.2 to classify headline and summary into one of our 12 master categories
def categorize_with_ollama(title : str, summary : str, depth : int = AGENT_RETRIES) -> str:
    # fallback to General if retries exhausted
    if depth <= 0:
        return 'General'

    global MASTER_CATEGORIES
    if not MASTER_CATEGORIES:
        MASTER_CATEGORIES = load_master_categories()
    try:
        response = ollama.chat(
            model='llama3.2',
            messages=[
                {'role' : 'system', 'content' : CATEGORIZATION_PROMPT},
                {'role' : 'user', 'content' : f'Title: {title}, Summary: {summary}'}
            ],
            format= 'json',
            options= {'temperature' : 0.1}
        )

        content = json.loads(response['message']['content'])
        category_id = content.get('category_id', 0)


        for name, id in MASTER_CATEGORIES.items():
            if category_id == id:
                return name
            

        return categorize_with_ollama(title, summary, depth - 1)
        
    except Exception as e:
        print(f'-_- [WARNING] Ollama encountered an error during processing...\n({depth}/{AGENT_RETRIES}): {e} ')
        return categorize_with_ollama(title, summary, depth - 1)
    
        


if __name__ == '__main__':
    main()
