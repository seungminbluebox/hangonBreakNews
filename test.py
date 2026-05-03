from llm_helper import safe_generate_content

def test():
    res = safe_generate_content('Hello, please respond with [{"test": 1}]')
    print("AI OUTPUT:", repr(res.text))

if __name__ == "__main__":
    test()