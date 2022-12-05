from .utils import *
from .Dtools import D_node,D_graph

# ==========================
# ====== S structure =======
# ==========================

class S_node():
    def __init__(self,code=None,protected=False,fct="",target="No target"):
        """
        A S_node is composed by one "real" computation, defining the
        "main_target", and followed by size / view operations over it.
        Attributes :
        .main_target : str
        .all_targets : str list
            -> names of all the vars defined
            -> (including .main_target)
        .tensor_targets : str list
            -> all_targets which are tensors
            -> (done by s_graph.make_tensor_targets)
        .main_code  : AST  : code to compute and assign .main_target
        .body_code  : AST  : code to assign all_targets except main_target
        .main_fct   : str  : fct used in .main_code
        .protected  : bool : see Doc
        .is_artefact: bool : see Doc
        .req        : (S_node,str set) dict = dict_edges
            -> required nodes with the vars needed per node.
        .used_by    : dict_edges : reciprocal of .req
        <TODO> : is_rand and req_rand ?
        """
        self.is_artefact = False
        self.main_code = code
        self.main_fct = fct
        self.body_code = [] # list of ast.Assign
        self.main_target = target # str
        self.all_targets = [target]
        self.tensor_targets = [] # later
        self.req = dict()
        self.used_by = dict()
        self.protected = protected
    def __eq__(self,n2):
        n1 = self
        b = check_attr(n1,n2,[
            "is_artefact","main_fct",
            "main_target","all_targets",
            "tensor_targets","protected"])
        b = (b
            and dict_edges_eq(n1.req,n2.req)
            and dict_edges_eq(n1.used_by,n2.used_by)
            and (n1.get_code() == n2.get_code()))
        return b
    def __hash__(self):
        return self.main_target.__hash__()
        # we use the assomption that a node is uniquely
        # defined by its .main_target within a graph

    def full_code(self):
        if self.main_code is None: mc = []
        else: mc = [self.main_code]
        return make_ast_module(mc + self.body_code)
    def get_code(self):
        return ast_to_str(self.full_code())

    def insert(self,aux_n,strong,g):
        # this is the fct to merge nodes : we insert "aux_n" in "self"
        # if strong: delete aux_n else aux_node becomes an artefact
        # in any case cut as many edges as possible

        merged_req = dict_edges_merge(self.req,aux_n.req)
        dict_edges_discard_inplace(merged_req,self)

        # -- disconnect aux_n with its children (if possible) --
        if strong: # e.g. for "view"
            dict_edges_discard_sn_from_req_of_its_users(aux_n)
            merged_used_by = dict_edges_merge(self.used_by,aux_n.used_by)
            dict_edges_discard_inplace(merged_used_by,aux_n)
            aux_n.used_by = dict()
        else: # e.g. for "size"
            for user_n in self.used_by.keys():
                dict_edges_discard_inplace(user_n.req,aux_n)
                dict_edges_discard_inplace(aux_n.used_by,user_n)
            merged_used_by = self.used_by
        # -- if aux_n is deleted, remove it from parents' used_by --
        if len(aux_n.used_by) == 0:
            dict_edges_discard_sn_from_used_by_of_its_req(aux_n)
            aux_n.req = dict()
            # -> aux_n has been fully unpluged
        else:
            aux_n.is_artefact = True
            # -> artefact

        # -- insert aux_n code --
        assert(aux_n.main_code is not None)
        self.body_code.append(aux_n.main_code)
        self.body_code.extend(aux_n.body_code)
        self.all_targets.extend(aux_n.all_targets)
        self.req = merged_req
        self.used_by = merged_used_by
        dict_edges_make_used_by_using_req(self)
        dict_edges_make_req_using_used_by(self)

        # -- special case if aux_n is the output --
        if aux_n is g.output_node:
            g.output_node = self

    def clear_children_artefact(self):
        # clean useless artefact children of self
        children = dict(self.used_by)
        for user_n in children.keys():
            if user_n.is_artefact:
                if set(user_n.req.keys()) != {self}:
                    s = ",".join(
                        [aux_n.main_target for aux_n in user_n.req])
                    raise Exception(
                      f"{self.main_target} should be the only parent of "\
                      f"{user_n.main_target} : {len(user_n.req)}\n{s}")
                for aux_n in self.used_by:
                    dict_edges_discard_edge(user_n,aux_n)
                if user_n.used_by == set():
                    dict_edges_discard_sn_from_used_by_of_its_req(user_n)
                    user_n.req = dict()

    def clear_siblings_artefact(self):
        real_req = set()
        for req_n in self.req.keys():
            if not req_n.is_artefact:
                real_req.add(req_n)
        for req_n in real_req:
            req_n.clear_children_artefact()

class S_graph():
    def __init__(self,dg : D_graph = None):
        self.nodes          = []
        self.init_node      = None
        self.output_node    = None
        self.hidden_inputs  = [] # str list
        self.direct_inputs  = [] # str list
        self.hidden_output  = "" # str
        self.direct_outputs = [] # str list
        self.dict_info      = {}
        self.dict_rand      = {}
        if dg:
            self.hidden_inputs  = dg.inputs
            self.direct_outputs = [dg.output]
            self.dict_info      = dg.dict_info
            self.dict_rand      = dg.dict_rand
    def __eq__(self,g2):
        return check_attr(self,g2,[
            "direct_inputs","hidden_inputs",
            "direct_outputs","hidden_output",
            "dict_info","nodes"])
    def __hash__(self):
        return id(self)

    def make_io(self):
        # assert : hidden_inputs & direct_outputs exist
        # assert : init_node & output_node exist
        # make direct_inputs & hidden_ouput
        self.hidden_output = self.output_node.main_target
        self.direct_inputs = (
            self.hidden_inputs + self.init_node.all_targets
        )


    def check_artefact(self):
        for n in self.nodes:
            if n.is_artefact:# and not (n is self.init_node):
                if len(n.req)!=1:
                    raise Exception(
                      f"{n.main_target} is_artefact, but with "\
                      f"len(req)={len(n.req) (should be 1)}")
                req_n = list(n.req.keys())[0]
                if dict_edges_is_subset(n.used_by,req_n.used_by):
                    # if n.used_by <= (req_n.used_by | set([n])): TO REMOVE
                    print(f"{n.main_target} is a useless "\
                          f"artefact of {req_n.main_target}")

    def check_relations(self):
        for n in self.nodes:
            for (req_n,str_set) in n.req:
                if (n not in req_n.used_by) or str_set != req_n.used_by[n]:
                    raise Exception(
                      f"{req_n.main_target} in {n.main_target}.req "\
                      f"but one sided relation...")
            for user_n in n.used_by:
                if (n not in user_n.req) or str_set != user_n.req[n]:
                    raise Exception(
                      f"{user_n.main_target} in {n.main_target}.used_by "\
                      f"but one sided relation...")

    def clear(self):
        # -- re-sorting nodes -- 
        # due to merging, the topo order may not be correct anymore
        # by the way, remove unpluged nodes
        self.nodes = sort_based_on_req(self.output_node)
        self.nodes.remove(self.init_node)
        self.check_artefact()
        self.check_relations()
        self.make_io()

    def make_tensor_targets(self):
        for n in self.nodes:
            if not n.is_artefact:
                l = []
                for tar in n.all_targets:
                    if self.dict_info[tar].ttype==torch.Tensor:
                        l.append(tar)
                n.tensor_targets = l


    def assert_ready(self):
        # check if ready to be given to S_to_K
        # ie main_targets are tensors, except if artefact -> sizes
        for n in self.nodes:
            if not (n.main_target in self.dict_info):
                raise Exception(
                  f"{n.main_target} not in dict_info ??")
            info = self.dict_info[n.main_target]
            if not (info.ttype in [torch.Tensor,torch.Size]):
                raise Exception(
                  f"After simplifications there should "\
                  f"only be tensors or sizes, but {info.ttype} "\
                  f"found for {n.main_target}.")
            if info.ttype==torch.Size and not n.is_artefact:
                raise Exception(
                  f"After simplifications, all remaining "\
                  f"\"size\" should be \"artefacts\", but "\
                  f"{n.main_target} isn't an artefact")


# ==========================


# ==========================
# = Init move from D to S  =
# ==========================

def D_to_S_init(dg : D_graph,keep_sequential=False) -> S_graph:
    global ref_keep_seq ; ref_keep_seq = keep_sequential
    sg = S_graph(dg)
    init_node = S_node(target="-- inputs --")
    init_node.all_targets=[]
    s_nodes = sg.nodes
    dict_s_nodes = {} # to translate D to S
    for dn in dg.nodes:
        sn = S_node(code=dn.ast_code,
                protected=dn.protected,
                fct=dn.fct,
                target=dn.target)
        s_nodes.append(sn)
        dict_s_nodes[dn.target] = sn
        for req_dn in dn.req:
            req_sn = dict_s_nodes[req_dn.target]
            sn.req[req_sn] = set((req_dn.target))
            req_sn.used_by[sn] = set((req_dn.target))
    # -- merge all the inputs in the special "init_node" --
    for inp in dg.inputs:
        init_node.insert(dict_s_nodes[inp],strong=True,g=sg)
    init_node.body_code = []
    sg.init_node = init_node
    sg.output_node = dict_s_nodes[dg.output]
    sg.clear()
    return sg

# ==========================



# ==========================
# ==== Simplification 1 ====
# === remove cheap nodes ===
# ==========================

def insert_ast_code(main_n,mc,target : str,sc):
    # mc : main_code , sc : sub_code
    assert(isinstance(mc,ast.Assign))
    assert(isinstance(sc,ast.Assign))
    assert(sc.targets[0].id == target)
    # if not ast.Assign -> simplifications haven't been done in
    # the right order ! (cheap -> size > view)
    # assert main_code is a one layer Call (no sub calls)
    scv = sc.value
    mcv = mc.value
    if isinstance(mcv,ast.Call):
        args = []
        kwds = []
        for s in mcv.args:
            if isinstance(s,ast.Name) and s.id == target:
                args.append(scv)
            else: args.append(s)
        for k in mcv.keywords:
            if isinstance(s,ast.Name) and s.id == target:
                kwds.append(scv)
            else: kwds.append(s)
        ret = ast.Call(mcv.func,args,kwds)
        main_n.main_code = ast.Assign(mc.targets,ret)
    elif (isinstance(mcv,ast.Tuple)
        or isinstance(mcv,ast.List)):
        l = []
        for s in mcv.elts:
            if isinstance(s,ast.Name) and s.id == target:
                l.append(scv)
            else: l.append(s)
        ret = type(mcv)(l)
        main_n.main_code = ast.Assign(mc.targets,ret)
    elif isinstance(mcv,ast.Subscript):
        assert(isinstance(scv,ast.List)
            or isinstance(scv,ast.Tuple))
        assert(len(mc.targets)==1)
        # mcv = scv.elts[mcv.slice.value]
        main_n.main_code = ast.Assign(mc.targets,scv.elts[mcv.slice.value])
        simplify_node(main_n)
    else:
        print(ast.dump(mc,indent=4))
        raise Exception(
            f"unknown type of code where we should "\
            f"insert things: {type(mc.value)}")

def simplify_node(sn):
    # aux fct, insert n.ast_code in children's code, and unplug it
    for user_sn in sn.used_by:
        # -- plug user_sn directly to req of sn --
        dict_edges_merge_inplace(user_sn.req,sn.req)
        dict_edges_discard_inplace(user_sn.req,sn)
        for (req_sn,str_set) in sn.req:
            dict_edges_discard_inplace(req_sn.used_by,sn)
            dict_edges_add_inplace(req_sn.used_by,user_sn,str_set)
        # -- insert the code --
        insert_ast_code(
            user_sn,user_sn.main_code,
            sn.main_target,sn.main_code)
    sn.req     = dict()
    sn.used_by = dict()

def simplify_cheap(sg : S_graph):
    # from root to leaves
    for sn in sg.nodes:
        if ( not (sn is g.output_node)
         and sn.main_fct in list_cheap_fct
         and (not ref_keep_seq or not sn.protected)):
            simplify_node(sn)
    g.clear()

# ==========================



# ==========================
# ==== Simplification 2 ====
# === insert size nodes ====
# ==========================

# 1) merge the size nodes which have the same parent
# 2) insert the size nodes in the body code of the
#    parent, and keep them only if needed -> artefact

def size_children(g,n):
    # give the list of child nodes of n which are size
    ret = []
    for user_n in n.used_by.keys():
        if g.dict_info[user_n.main_target].ttype == torch.Size:
            ret.append(user_n)
    return ret


def simplify_size(g : S_graph):
    # from leaves to root
    nodes = [g.init_node] + list(g.nodes) ; nodes.reverse()
    for n in nodes:
        if not n is g.output_node:
            list_size = size_children(g,n)
            if list_size != []:
                # -- merge into one node --
                size_n = list_size[0]
                for other_n in list_size[1:]:
                    size_n.insert(other_n,strong=True,g=g)
                # -- insert their code --
                if n is g.init_node:
                    n.insert(size_n,strong=True,g=g)
                else: n.insert(size_n,strong=False,g=g)
    g.clear()

# ==========================



# ==========================
# ==== Simplification 3 ====
# === remove view nodes ====
# ==========================

def simplify_view(g):
    # from root to leaves
    g.init_node.is_artefact = True
    for n in g.nodes:
        #if ( n.main_target != g.output
        #    and (not ref_keep_seq or not n.protected)
         if n.main_fct in list_view_fct or n.main_fct == "getattr":
            # /!\ ASSERTION remaining getattr are related to views !! 
            real_req = []
            for req_n in n.req.keys():
                if not req_n.is_artefact:
                    real_req.append(req_n)
            if len(real_req)==1:
                req_n = real_req[0]
                req_n.insert(n,strong=True,g=g)
                req_n.clear_siblings_artefact()
            elif len(real_req)==0 and len(n.req)>0:
                # experimental : I assume that views which don't 
                # require any real tensor are views over parameters
                # so mem=0 and no bwd K_node, so I can insert them
                # in their parents even if they are artefacts.
                # But artefact nodes aren't safe, they might disappear
                # if self.used_by sub set of self.parent.used_by
                # so I must share the code with artifacts' parents
                # I can insert the code in many different nodes
                # because views operations are cheap.
                # But I must avoid creating cycle dependancies, so
                # for the moment I assert len(n.req)==1
                if len(n.req)>1: print(
                    f"Warning : {n.main_target} is a view op, but without"\
                    f" a real parent, and several artifact dependancies",
                    file = sys.stderr)
                else:
                    art_req = list(n.req.keys())[0]
                    assert(len(art_req.req)==1) # as an artefact
                    real_req = list(art_req.req.keys())[0]
                    # - Insert n's code both in art_req and real_req -
                    for aux_n in [art_req,real_req]:
                        aux_n.body_code.append(n.main_code)
                        aux_n.body_code.extend(n.body_code)
                    # - plug art_req to n's users -
                    dict_edges_merge_inplace(art_req.used_by,n.used_by)
                    for (user_n,str_set) in n.used_by:
                        dict_edges_add_inplace(user_n.req,art_req,str_set)
                    # - unplug n -
                    dict_edges_discard_inplace(art_req.used_by,n)
                    dict_edges_discard_sn_from_req_of_its_users(n)
                    n.req = dict()
                    n.used_by = dict()
                    real_req.clear_children_artefact()

    g.clear()

# ==========================



# ==========================
# = Move from D to S graph =
# ==========================

def D_to_S(dg,keep_sequential=False):
    sg = D_to_S_init(dg,keep_sequential)
    simplify_cheap(sg)
    simplify_size(sg)
    simplify_view(sg)
    sg.check_relations()
    sg.make_tensor_targets()
    sg.assert_ready()
    return sg

# ==========================



# ==========================
# ==== Cut the graph in ====
# ==== sequential parts ====
# ==========================

def copy_node(n : S_node): # aux for copy_graph
    new_n = S_node()
    new_n.is_artefact    = n.is_artefact
    new_n.main_code      = n.main_code
    new_n.main_fct       = n.main_fct
    new_n.body_code      = list(n.body_code)
    new_n.main_target    = n.main_target
    new_n.all_targets    = list(n.all_targets)
    new_n.tensor_targets = list(n.tensor_targets)
    new_n.req            = dict() # /!\
    new_n.used_by        = dict() # /!\
    new_n.protected      = n.protected
    return new_n

def copy_graph(g : S_graph):
    # -> a copy of g with fresh nodes
    new_g = S_graph()
    new_g.hidden_inputs  = list(g.hidden_inputs)
    new_g.direct_inputs  = list(g.direct_inputs)
    new_g.hidden_output  = g.hidden_output
    new_g.direct_outputs = list(g.direct_outputs)
    new_g.dict_info      = g.dict_info
    new_g.dict_rand      = g.dict_rand
    dict_nodes = {}
    new_init = copy_node(g.init_node)
    new_nodes = []
    dict_nodes[new_init.main_target] = new_init
    for n in g.nodes:
        new_n = copy_node(n)
        new_nodes.append(new_n)
        dict_nodes[n.main_target] = new_n
        for (req_n,set_str) in n.req:
            new_req = dict_nodes[req_n.main_target]
            dict_edges_add_inplace(new_req.used_by,new_n,set_str)
            dict_edges_add_inplace(new_n.req,new_req,set_str)
    new_g.init_node     = new_init
    new_g.output_node   = dict_nodes[g.hidden_output]
    new_g.nodes         = new_nodes
    return new_g


def cut(g : S_graph): # -> list of S_graph
    main_g = copy_graph(g) # to protect from side effects
    main_g.nodes.insert(0,main_g.init_node)
    seps = cut_based_on_req(main_g)
    print_debug(f"S separators : {[sep.main_target for sep in seps]}")
    list_g = []
    for i in range(1,len(seps)):
        new_g = S_graph()
        list_g.append(new_g)
        # -- get nodes --
        inp_node = seps[i-1]
        out_node = seps[i]
        inp_i = main_g.nodes.index(inp_node)
        out_i = main_g.nodes.index(out_node)
        nodes = main_g.nodes[inp_i+1:out_i+1]
        new_g.nodes = nodes
        print_debug(f"size of bloc {i} : {out_i}-{inp_i}")
        # -- input --
        if i==1:
            new_g.init_node = main_g.init_node
            new_g.hidden_inputs = main_g.hidden_inputs
            new_g.direct_inputs = main_g.direct_inputs
        else:
            ino = S_node(
                target=f"init_node of bloc {i}>1, should NEVER be used")
            new_g.hidden_inputs = [inp_node.main_target]
            new_g.direct_inputs = inp_node.all_targets
            new_g.init_node = ino
            for (user_n,str_set) in inp_node.used_by:
                dict_edges_discard_inplace(user_n.req,inp_node)
                dict_edges_add_inplace(user_n.req,ino,str_set)
                dict_edges_add_inplace(ino.used_by,user_n,str_set)
            inp_node.used_by = dict() # previous bloc's output node
        # -- output --
        new_g.output_node    = out_node
        new_g.hidden_output  = out_node.main_target
        new_g.direct_outputs = out_node.all_targets
        # --
        new_g.dict_info = main_g.dict_info
        new_g.dict_rand = main_g.dict_rand
    return list_g

# ==========================



# ==========================
# === printing functions ===
# ==========================

def aux_print_graph(dot,g,uniq_num):
    def uni(tar): return f"_{uniq_num}_{tar}"
    def node(i,l,**kwargs): dot.node(uni(i),l,**kwargs)
    def edge(i1,i2): dot.edge(uni(i1),uni(i2))
    str_ino = g.init_node.main_target
    node(str_ino,g.init_node.get_code(),style="dashed")
    for n in g.nodes:
        if n.is_artefact:
            node(n.main_target,n.get_code(),style="dashed")
        else: node(n.main_target,n.get_code())
    for n in g.nodes:
        for sub_n in n.req:
            edge(sub_n.main_target,n.main_target)

    # -- io --
    str_inp = "\n".join(g.direct_inputs)
    node("input",
        f"INPUT : {str_inp}",
        color="green",style="dashed")
    str_out = "\n".join(g.direct_outputs)
    node("output",
        f"OUTPUT : {str_out}\nhidden : {g.hidden_output}",
        color="green",style="dashed")
    edge("input",g.init_node.main_target)
    edge(g.hidden_output,"output")


def print_S_graph(g : S_graph,name=None,open=True):
    print(f"Simplified forward graph : {len(g.nodes)} nodes")
    if name is None: name = "forward S-graph"
    dot = graphviz.Digraph(
        name,
        comment="S_graph = Simplified forward graph")
    aux_print_graph(dot,g,0)
    graph_render(dot,open,"S") # from utils.py


def print_S_graph_list(list_g,name=None,open=True):
    s = "+".join([str(len(g.nodes)) for g in list_g])
    print(
        f"{len(list_g)} blocs of S_graph, with {s} = "\
        f"{sum([len(g.nodes) for g in list_g])} nodes")
    if name is None: name = "cut forward S-graph"
    dot = graphviz.Digraph(
        name,
        comment="S_graph list : cut simplified forward graph")
    for i in range(len(list_g)):
        aux_print_graph(dot,list_g[i],i)
    graph_render(dot,open,"S") # from utils.py

# ==========================

