import make_trace
import ast, sys, json
import traceback

DEBUG = True
decoder = json.JSONDecoder()

def process_one(outfile, string):
    obj = decoder.decode(string)
    source = obj['user_script']
    ri = json.dumps(obj['raw_input']) if 'raw_input' in obj else '[]'

    last_line = len(source.splitlines())
    slice_lines, slice_p = make_trace.slice(source, ri, debug=True)

    if slice_lines:
        print('Original code:')
        print(source)

        print('Lines to keep: ' + str(slice_lines))
        print('Line proportion removed: ' + str(slice_p))

        obj['exception_slice'] = list(slice_lines)
    else:
        print("No exception")

    json.dump(obj, outfile)

with open(sys.argv[1]) as infile:
    with open(sys.argv[1] + '.sliced', 'wt') as outfile:

        for i, line in enumerate(infile):
            print('Interaction ' + str(i))
            try:
                process_one(outfile, line)
            except Exception as e:
                if DEBUG:
                    traceback.print_exc(None, sys.stdout)
                else:
                    print(e)
            print('')
