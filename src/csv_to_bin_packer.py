import csv
import struct
import sys
import os
import time
import json
from datetime import datetime
# Format: uint64(x4), uint32, uint8(x2), char[4], 256s+H, 64s+H, 564s+H, 256s+H
RECORD_FORMAT = '<QQQQIHH256sH64sH564sH256sH'
ONE_YEAR_TIMESTAMP = 365 * 24 * 60 * 60
AUTHOR_LENGTH = 64
TITLE_LENGTH = 256
SUMMARY_LENGTH = 256
LINK_LENGTH = 564
STRUCT_SIZE = 1188
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CATEGORY_MAP_PATH = os.path.join(SCRIPT_DIR, 'ai_agent', 'json', 'category_lookup.json')
MASTER_CATEGORIES_PATH = os.path.join(SCRIPT_DIR, 'ai_agent', 'json', 'master_categories.json')

category_dict = {'General' : 0}
next_category_id = 1
category_map = {}
master_categories = {}

csv.field_size_limit(2147483647)


def main():
    #csv_path = input("Enter CSV filepath: ")
    #bin_path = input("Enter Bin filepath: ")
    csv_path = "F:\\projects\\oop-python\\input_data_csv\\all-the-news-2-1.csv"
    bin_path = "F:\\projects\\oop-python\\injector_src\\packed"
    pack_dataset(csv_path, bin_path)
    wait_for_user()

def load_category_map(map_path : str = CATEGORY_MAP_PATH):
    if not os.path.exists(map_path):
        return {}
    
    with open(map_path, 'r', encoding='utf-8') as file:
        return json.load(file)

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


def map_category(name : str) -> int:
    global category_map, master_categories
    if not category_map:
        category_map = load_category_map()
    if not master_categories:
        master_categories = load_master_categories()
    if name in master_categories:
        return master_categories.get(name, 0)
    return category_map.get(name, 0)

def get_or_create_category(section_name: str) -> int:
    global next_category_id
    global category_dict
    section_name = str(section_name).strip()

    if not section_name:
        section_name = 'General'
    # keep mapping the categories for the cleaner
    if section_name not in category_dict:
        if next_category_id < 65535:
            category_dict[section_name] = next_category_id
            next_category_id += 1
        else:
            # return general category
            return 0

    return map_category(section_name)


def encode_fixed_string(text: str, max_length: int) -> tuple[bytes, int]:
    if not text:
        text = ""

            
    raw_bytes = str(text).encode('utf-8', errors='ignore')

    truncated_bytes = raw_bytes[:max_length - 1]
    actual_length = len(truncated_bytes)
    padded_bytes = truncated_bytes.ljust(max_length, b'\x00')

    return padded_bytes, actual_length

def parse_unix_time(row: dict) -> int:
    try:
        # get year month day
        year = int(float(row.get('year', 0)))
        month = int(float(row.get('month', 0)))
        day = int(float(row.get('day', 0)))
        # check date for basic validation
        if year > 1900 and 1 <= month <= 12 and 1 <= day <= 31:
            date_object = datetime(year, month, day)
            return int(time.mktime(date_object.timetuple()))
    except Exception:
        pass

    return int(time.time()) # return current time if date extraction fails

# takes a csv dict objects to raw binary bytes packed into the custom struct
def row_to_record(row: dict) ->bytes:
    #id will given by the record manager
    record_id = 0
    if not row.get('published_on', 0):
        published_on = parse_unix_time(row)
    else:
        published_on = int(row.get('published_on', time.time()))
    created_on = int(time.time())
    expires_at = created_on + ONE_YEAR_TIMESTAMP

    views = 1
    flags = 0
   

    category_id = get_or_create_category(row.get('section', 'General'))

    title_b, title_l = encode_fixed_string(row.get('title', 'Unknown'), TITLE_LENGTH)

    # author extraction
    raw_author = str(row.get('author', '')).strip()

    if not raw_author:
        raw_author = str(row.get('publication', 'Unknown')).strip()

    author_b, author_l = encode_fixed_string(raw_author, AUTHOR_LENGTH)

    link_b, link_l = encode_fixed_string(row.get('url', 'www.dummy.com'), LINK_LENGTH)

    full_article = row.get('article', 'No content to show.')

    summary_b, summary_l = encode_fixed_string(full_article[:250], SUMMARY_LENGTH)

    # DONOT TOUCH HARD CONFIGURED TO news_record
    # using struct to pack the data in strict format
    return struct.pack(
        RECORD_FORMAT,
        record_id, published_on, created_on, expires_at,       # QQQQ (32 bytes)
        views, category_id, flags,                             # IHH (8 bytes)
        title_b, title_l,                                      # 256s H (258 bytes)
        author_b, author_l,                                    # 64s H (66 bytes)
        link_b, link_l,                                        # 564s H (566 bytes)
        summary_b, summary_l                                   # 256s H (258 bytes) 
    )


def generate_cpp_category_header(output_filename = "category_map.h"):
    print(f"Generating C++ Header: {output_filename}...")
    
    # 1. Build the dynamic switch cases in memory first
    switch_cases = ""
    for name, cat_id in category_dict.items():
        safe_name = name.replace('"', '\\"')
        switch_cases += f'            case {cat_id}: return "{safe_name}";\n'

    
    cpp_code = f"""
#pragma once
#include <string>
#include <cstdint>

namespace data {{
    // Auto-generated by Python Packer
    inline std::string get_category_name(std::uint16_t id) {{
        switch(id) {{
{switch_cases}            
            case 65535: return "All Categories";
            default: return "Unknown";
        }}
    }}
}}
    """

    # write the payload onto the file
    with open(output_filename, 'w', encoding='utf-8') as f:
        f.write(cpp_code)

    print(f"Header Generated Successfully")



def pack_dataset(csv_filepath: str, bin_dir: str):
    bin_filepath = os.path.join(bin_dir, "packed_news.bin")
    print(f"Opening {csv_filepath}...")
    success_count = 0
    if not os.path.exists(csv_filepath):
        print(f"ERROR: Cannot find {csv_filepath}! Please check the folser.")
        return

    with open(csv_filepath, 'r', encoding='utf-8') as csv_file, \
        open(bin_filepath, 'wb') as bin_file:

        reader = csv.DictReader(csv_file)
        for row in reader:
            # skip empyt data
            title = str(row.get('title', '')).strip()
            article = str(row.get('article', '')).strip()
            if not title and not article:
                continue
            # try to pack the row
            try:
                binary_payload = row_to_record(row)
                bin_file.write(binary_payload)
                success_count += 1
            except Exception:
                pass # pass if the packing fails

            if success_count % 50000 == 0:
                clear_screen()
                print(f"Records Packed: {success_count}")
            

    print(f"Successfully packed {success_count} records into '{bin_filepath}'!")
    print(f"Total File Size: {(success_count * STRUCT_SIZE) / (1024*1024):.2f} MB")
    generate_cpp_category_header()

def wait_for_user():
    input("Press Enter to continue..")

def clear_screen():
    print("\033[H\033[J", end='')


if __name__ == "__main__":
    main()
    
