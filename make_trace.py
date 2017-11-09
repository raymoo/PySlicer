import sys, os, ast
import pg_logger
from collections import defaultdict
from queue import Queue

def trace(source):
    def finalizer(input_code, output_trace):
        return output_trace
    
    return pg_logger.exec_script_str_local(source,
                                           '[]',
                                           True,
                                           True,
                                           finalizer)

class LineMapVisitor(ast.NodeVisitor):
    def __init__(self, the_map):
        self.the_map = the_map

    def visit(self, node):
        if isinstance(node, ast.stmt):
            self.the_map[node.lineno] = node
        self.generic_visit(node)

"""
Currently only handles the case where statements are on separate lines.

TODO: Transform the original source so that statements are always on separate
lines.
"""
def make_line_map(source):
    the_map = {}
    astree = ast.parse(source)
    visitor = LineMapVisitor(the_map)
    visitor.visit(astree)

    return the_map

# TODO: Support locals
def name_to_ref(exec_point, name):
    [tag, ref] = exec_point['globals'][name]
    assert(tag == 'REF')
    return ref

# TODO: Support locals
def name_to_var(exec_point, name):
    if name in exec_point['globals']:
        return "global:" + name

    return "undefined:" + name

class UseVisitor(ast.NodeVisitor):
    def __init__(self, exec_point, use_set):
        self.exec_point = exec_point
        self.use_set = use_set

    def die(self, node):
        raise ValueError('Unsupported node: ' + type(node))

    # Exprs
    # BoolOp
    # BinOp
    # UnaryOp
    visit_Lambda = die
    # IfExp
    visit_Dict = die
    visit_Set = die
    visit_ListComp = die
    visit_SetComp = die
    visit_DictComp = die
    visit_GeneratorExp = die
    visit_Await = die
    visit_Yield = die
    visit_YieldFrom = die
    # Compare
    # Call
    # Num
    # Str
    # FormattedValue
    # JoinedStr
    # Bytes
    # NameConstant
    visit_Ellipsis = die
    # Constant
    visit_Attribute = die
    visit_Subscript = die
    visit_Starred = die

    def visit_Name(self, node):
        self.use_set.add(name_to_ref(self.exec_point, node.id))
        self.use_set.add(name_to_var(self.exec_point, node.id))

    # List
    # Tuple

    # Stmts
    visit_FunctionDef = die
    visit_AsyncFunctionDef = die
    visit_ClassDef = die
    visit_Return = die
    visit_Delete = die # Not sure what kinds of expressions this refers to

    def visit_Assign(self, stmt):
        self.visit(stmt.value)

    def visit_AugAssign(self, stmt):
        for target in stmt.targets:
            self.visit(target)
        self.visit(stmt.value)

    visit_AnnAssign = visit_Assign
    visit_For = die
    visit_AsyncFor = die
    visit_While = die
    visit_If = die
    visit_With = die
    visit_AsyncWith = die
    # Raise
    visit_Try = die
    # Assert
    visit_Import = die
    visit_ImportFrom = die
    visit_Global = die
    visit_Nonlocal = die
    # Expr
    # Pass
    # Break
    # Continue

"""
Does not currently handle that an expression could construct multiple objects.
The trickiness is that even if we know an object will be constructed on the
heap, we still don't know what reference id it will get when executed.
"""
class DefineVisitor(ast.NodeVisitor):
    def __init__(self, exec_point, define_set):
        self.exec_point = exec_point
        self.define_set = define_set

    def die(self, node):
        raise ValueError('Unsupported node: ' + type(node))

    # Exprs
    # BoolOp
    # BinOp
    # UnaryOp
    visit_Lambda = die
    # IfExp
    visit_Dict = die
    visit_Set = die
    visit_ListComp = die
    visit_SetComp = die
    visit_DictComp = die
    visit_GeneratorExp = die
    visit_Await = die
    visit_Yield = die
    visit_YieldFrom = die
    # Compare
    # Call
    # Num
    # Str
    # FormattedValue
    # JoinedStr
    # Bytes
    # NameConstant
    visit_Ellipsis = die
    # Constant
    visit_Attribute = die
    visit_Subscript = die
    visit_Starred = die

    def visit_Name(self, node):
        if isinstance(node.ctx, (ast.Store, ast.Del, ast.AugStore)):
            # TODO: Support mutable objects
            self.define_set.add(name_to_var(self.exec_point, node.id))

    # List
    # Tuple

    # Stmts
    visit_FunctionDef = die
    visit_AsyncFunctionDef = die
    visit_ClassDef = die
    visit_Return = die
    visit_Delete = die # Not sure what kinds of expressions this refers to

    def visit_Assign(self, stmt):
        for target in stmt.targets:
            self.visit(target)

    def visit_AugAssign(self, stmt):
        for target in stmt.targets:
            self.visit(target)

    visit_AnnAssign = visit_Assign
    visit_For = die
    visit_AsyncFor = die
    visit_While = die
    visit_If = die
    visit_With = die
    visit_AsyncWith = die
    # Raise
    visit_Try = die
    # Assert
    visit_Import = die
    visit_ImportFrom = die
    visit_Global = die
    visit_Nonlocal = die
    # Expr
    # Pass
    # Break
    # Continue
    
def used_stmt(exec_point, stmt):
    use_set = set()
    UseVisitor(exec_point, use_set).visit(stmt)
    return use_set

def defined_stmt(exec_point, stmt):
    define_set = set()
    DefineVisitor(exec_point, define_set).visit(stmt)
    return define_set

# Returns a map from steps to lines and a combined UD and CT "multimap"
def build_relations(line_map, tr):
    # UD instead of DU, so we can go use -> definition. Similarly, use CT
    # instead of TC
    UD_CT = defaultdict(set)
    step_to_line = {}

    # Reference to step
    last_definitions = {}

    for step, exec_point in enumerate(tr):
        if exec_point['event'] != 'step_line':
            continue

        line = exec_point['line']
        stmt = line_map[line]

        step_to_line[step] = line

        stmt_useds = used_stmt(exec_point,  stmt)
        stmt_defineds = defined_stmt(tr[step + 1], stmt)

        for ref in stmt_useds:
            if ref in last_definitions:
                UD_CT[step].add(last_definitions[ref])

        # Could it be simpler to check which refs are changed by diffing the heap?
        for ref in stmt_defineds:
            last_definitions[ref] = step

    # TODO: CT

    return step_to_line, UD_CT

"""
Returns a set of line numbers.

Possible improvement: Allow specifying a set of locations used in the statement
to slice, instead of all of them
"""

def slice_program(source, step):
    line_map = make_line_map(source)
    tr = trace(source)

    step_to_line, UD_CT = build_relations(line_map, tr)

    visited = set()
    queue = Queue()
    queue.put(step)

    while not queue.empty():
        step = queue.get()
        if step in visited:
            continue
        visited.add(step)

        # Put influencing steps in the queue
        for infl_step in UD_CT[step]:
            queue.put(infl_step)

    return sorted([step_to_line[step] for step in visited])
