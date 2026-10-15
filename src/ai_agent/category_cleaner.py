import os
import re
import json
import ollama

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OLLAMA_MODEL = 'llama3.2'
DEFAULT_DICT = {'General' : 0}
BATCH_SIZE = 30
SAVE_FREQUENCY = 300
AGENT_RETRIES = 5
LOOKUP_JSON_PATH = os.path.join(SCRIPT_DIR, 'json', 'category_lookup.json')
HEADER_PATH = os.path.join(SCRIPT_DIR, "category_map.h")
SYSTEM_PROMPT = """You are a strict data classification engine.
    Map each input news section name (given as a JSON object of {"index": "section_name"}) to the single best Category ID (an integer from 0 to 11):
    0: General (catch-all for vague, meaningless, magazine issue titles, slang, or unclassifiable strings)
    1: World News (countries, international affairs, regions, wars, foreign policy)
    2: Politics (elections, government, politicians, congress, policy)
    3: Business & Finance (markets, stocks, bonds, companies, economy, deals, real estate)
    4: Technology (software, hardware, internet, AI, cyber, gaming, tech brands)
    5: Science (space, physics, biology, biotech, research)
    6: Health (medicine, disease, healthcare, mental health, drugs, fitness)
    7: Sports (leagues, athletes, Olympics, NBA, MLB, racing, football)
    8: Entertainment & Arts (music, film, TV, art, comics, celebrity, culture)
    9: Environment (climate, energy, weather, sustainability, nature, wildfires)
    10: Crime & Law (police, courts, legal news, prison, true crime, shootings)
    11: Lifestyle (food, travel, fashion, relationships, horoscopes, advice)
    Respond ONLY with a valid JSON object mapping each input index string to its integer ID (0-11).
    Example output: {"0": 3, "1": 1, "2": 0}"""



# Our 12 Clean Master Categories
MASTER_CATEGORIES = {
    0: "General",
    1: "World News",
    2: "Politics",
    3: "Business & Finance",
    4: "Technology",
    5: "Science",
    6: "Health",
    7: "Sports",
    8: "Entertainment & Arts",
    9: "Environment",
    10: "Crime & Law",
    11: "Lifestyle"
}

def load_lookup(json_path: str) -> dict[str, int]:

    if not os.path.exists(json_path):
        return DEFAULT_DICT


    # verify if the data is a dict of required signature
    try:
        # get the dict object through json.load
        with open(json_path, 'r', encoding='utf-8') as file:
            data = json.load(file)

        if not isinstance(data, dict):
            return DEFAULT_DICT.copy()
        # verify each key value pair
        validated_data: dict[str, int] = DEFAULT_DICT.copy()
        # only include validated k/v pairs existing in master categories
        for key, value in data.items():
            if (isinstance(key, str) and isinstance(value, int)
            and not isinstance(value, bool) and value in MASTER_CATEGORIES):
                validated_data[key] = value
            
        return validated_data    

    except Exception as e:
        print(f'Error encountered when loading json data from:\n{json_path}:\n{e}\nResettig lookup')
        return DEFAULT_DICT.copy()

def save_lookup(lookup: dict[str, int], json_path: str):
    
    dirs = os.path.dirname(json_path)
    # create directory if the user provides it
    if dirs:
        # check if the direcotory exists, if not then create it
        # makedir handles all that logic in itself
        os.makedirs(dirs, exist_ok=True)
    # dump the dict in json format in the file
    with open(json_path, 'w', encoding='utf-8') as file:
        json.dump(lookup, file, indent=2, ensure_ascii=False)



def extract_raw_categories(header_path: str) -> list[str]:
    """Reads category_map.h and extracts all raw category strings using Regex."""
    if not os.path.exists(header_path):
        print(f"ERROR: Could not find {header_path}")
        return []

    with open(header_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Regex expression to capture all the section names;
    raw_names = re.findall(r'case \d+: return "(.*?)";', content)

    # Filter out the special 65535 "All Categories" case
    clean_list = [name for name in raw_names if name != "All Categories"]
    return clean_list

# takes list of section names and returns a dict with the sections
# mapped to master categories through Ollama 3.2, will try set amount times
# before giving up
def classify_batch(batch : list[str], depth: int = AGENT_RETRIES) -> dict[str, int]:

    if depth <= 0:
        return DEFAULT_DICT.copy()
    # created indexed dict with given batch
    indexed_input = {str(index): name for index, name in enumerate(batch)}

    if not indexed_input:
        return DEFAULT_DICT.copy()
        

    # try to process the batch
    try:
        # call the agent to process the batch
        reponse = ollama.chat(
            model=OLLAMA_MODEL,
            format='json',
            options={'temperature' : 0.0}, # 0.0 deterministic
            messages=[
                {'role' : 'system', 'content' : SYSTEM_PROMPT},
                {'role' : 'user', 'content' : json.dumps(indexed_input)} #pass the dict input as string
            ]
        )
        # make the dict with the response
        agent_output = json.loads(reponse['message']['content'])
        # process the agent output
        mapped_batch : dict[str, int] = {}
        # if all good process the output
        for index, name in enumerate(batch):
            # return 0 if key not found
            raw_id = agent_output.get(str(index), 0)
    
            try:
                category_id = int(raw_id)
                if category_id not in MASTER_CATEGORIES:
                    category_id = 0
            except (ValueError, TypeError):
                category_id = 0
    
            mapped_batch[name] = category_id

    except Exception:
        return classify_batch(batch, depth - 1)

    return mapped_batch


def main():
    print('Starting Category Cleaner...')
    print('Extracting categories from header...')
    extracted_categories = extract_raw_categories(HEADER_PATH)
    print('Categories Exracted.')
    print('Checking and loading previous state...')
    categorized_count = 0
    mapped_categories : dict[str, int] = load_lookup(LOOKUP_JSON_PATH)
    if not mapped_categories:
        print('No saved state found, Starting fresh...')
    else:
        print('Previos state loaded...')

    print('Initiating Categorization...')
    # get remaining categories that are yet to be processed or failed
    remaining_categories = [name for name in extracted_categories if name not in mapped_categories]
    for index in range(0, len(remaining_categories), BATCH_SIZE):
        print(f'Agent is processing Batch: {index}->{index + BATCH_SIZE}...')
        # call the agent to get the categories
        category_batch = classify_batch(remaining_categories[index : index + BATCH_SIZE])
        # skip current iteration if the batch failed
        if category_batch == DEFAULT_DICT:
            print(f'Batch: {index}->{index + BATCH_SIZE} [Failure].')
            continue
        
        print(f'Batch: {index}->{index + BATCH_SIZE} [Success].')

        categorized_count += BATCH_SIZE
        # update the overall dictionary
        mapped_categories.update(category_batch)

        if categorized_count % SAVE_FREQUENCY == 0:
            save_lookup(mapped_categories, LOOKUP_JSON_PATH)

    print(f'Total categories cleaned: {categorized_count}')
    print(f'{len(mapped_categories)} of {len(extracted_categories)} categorized.')
    # final save
    print(f'Saving mapped categories')
    save_lookup(mapped_categories, LOOKUP_JSON_PATH)
    print(f'Mapped categories saved to {LOOKUP_JSON_PATH}')
    input('Press Enter to exit...')

if __name__ == "__main__":
    main()
