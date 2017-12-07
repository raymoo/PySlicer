import make_trace
import ast, sys, json
import traceback

DEBUG = False
decoder = json.JSONDecoder()

class RemoveTransformer(ast.NodeTransformer):
    def __init__(self, keep_set):
        self.keep_set = keep_set

    def visit(self, node):
        if isinstance(node, ast.stmt):
            if node in self.keep_set:
                self.generic_visit(node)
            else:
                return ast.Pass()
        return node

def process_one(string):
    obj = decoder.decode(string)
    source = obj['user_script']
    ri = json.dumps(obj['raw_input']) if 'raw_input' in obj else '[]'

    last_line = len(source.splitlines())
    slice_lines, slice_p = make_trace.slice(source, ri, debug=True)
    astree = ast.parse(source)
    visitor = RemoveTransformer(slice_lines)
    astree = visitor.visit(astree)

    if slice_lines:
        print('Original code:')
        print(source)

        print('Lines to keep: ' + str(slice_lines))
        print('Line proportion removed: ' + str(slice_p))
    else:
        print("No exception")

file = open(sys.argv[1])

for i, line in enumerate(file):
    print('Interaction ' + str(i))
    try:
        process_one(line)
    except Exception as e:
        if DEBUG:
            traceback.print_exc(None, sys.stdout)
        else:
            print(e)
    print('')
