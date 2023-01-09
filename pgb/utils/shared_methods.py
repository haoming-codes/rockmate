# ========================================
# = Useful functions for PGB's graphs
# = for instance methods with similar code
# ========================================
from pgb.utils.imports import *
from pgb.utils.ast_add_on import (
    make_str_assign, make_str_list_assign)

# ======================================
# ==== GENERATE STR CODE FOR S AND K==== 
# ======================================

def get_code(n): # For S_node or KCN
    dict_ic = dict(n.inplace_code)
    bc = [
        (tar,dict_ic[tar] if tar in dict_ic else acode)
        for (tar,acode) in n.body_code]
    mc = make_str_assign(n.main_code)
    mc = "" if mc == "" else mc+"\n"
    bc = make_str_list_assign(bc)
    return mc+bc

def full_code(n): # For S_node or KCN
    # This function is a way to produce what the final
    # code will look like (including detach). But it's
    # never used in RK, the translator isn't that simple.
    mt = n.main_target
    mc = make_str_assign(n.main_code,prefix="_")
    ic = make_str_list_assign(n.inplace_code)
    bc = make_str_list_assign(n.body_code)
    if mc == "":
        return bc
    else:
        s = f"{mc}\n{mt} = _{mt}\n"
        s += ic+"\n" if ic != "" else ""
        s += f"{mt} = _{mt}.detach().requires_grad_()\n"
        s += bc
        return s

# ==========================



# ===============================
# = GENERAL FUNCTIONS TO GET    =
# = NODE'S TARGET, NUM AND DEPS =
# ===============================

def get_target(n):
    try: return n.target
    except: return n.main_target

def get_num_tar(tar):
    try:    return int(tar.split('_')[2])
    except: return (-1)
def get_num_name(name): # for KCN or KDN's name
    if (name.startswith("fwd_")
    or  name.startswith("bwd_")):
        return get_num_tar(name[4:])
    elif (name.endswith("data")
    or    name.endswith("grad")):
        return get_num_tar(name[:-4])
    elif name.endswith("phantoms"):
        return get_num_tar(name[:-8])
def get_num(n): # can be used on B, D, S or K
    return get_num_tar(get_target(n))

sort_nodes = lambda s : sorted(s,key=get_num)
sort_targets = lambda s : sorted(s,key=get_num_tar)
sort_names = lambda s : sorted(s,key=get_num_name)

def get_deps(n):
    # To be compatible with different type/name of attribute "deps"
    t = str(type(n))
    if   "B_node" in t:   return n.deps
    elif "D_node" in t:   return n.deps
    elif "S_node" in t:   return set(n.deps.keys())
    elif "K_C_node" in t: return set().union(
        *[kdn.deps for kdn in n.deps_real],
        n.deps_through_size_artefacts)
    elif "K_D_node" in t: return set().union(
        *[kcn.deps_real for kcn in n.deps])
    else: raise Exception(f"Unrecognize node type : {t}")

# ==========================



# ==========================
# ==== PERFECT TOPOSORT ====
# ==========================

def sort_based_on_deps(origin_node): # used on B, S and K
    # /!\ origin_node is the root of .deps relation 
    # /!\ => e.g. the output node of the graph

    # Compute incomming degree
    degree = {}
    def count_edges(n):
        for sub_n in get_deps(n):
            if sub_n not in degree:
                d = 0
                degree[sub_n] = 0
                count_edges(sub_n)
            else:
                d = degree[sub_n]
            degree[sub_n] = d+1
    count_edges(origin_node)

    # Explore nodes by increasing lexi-order of their n.target
    # BUT a node is explored iff all its users are explored => toposort
    sorted_list = []
    to_explore = set([origin_node])
    while to_explore: # not empty
        n = max(to_explore,key=lambda n : get_num(n))
        to_explore.discard(n)
        sorted_list.append(n)
        for req_n in get_deps(n):
            if req_n in sorted_list:
                raise Exception("Cycle in the graph => no toposort")
            d = degree[req_n]
            if d == 1:
                to_explore.add(req_n)
            else:
                degree[req_n] = d-1

    # return from first to last
    return sorted_list[::-1]

# ==========================



# ==========================
# ======= CUT GRAPHS =======
# ==========================

def cut_based_on_deps(g): # used on D and S
    # returns the list of all 1-separator of the graph.
    to_be_visited = [g.output_node]
    seen = set([g.output_node])
    dict_nb_usages = dict([(m , len(m.users)) for m in g.nodes])
    separators = []
    while to_be_visited!=[]:
        n = to_be_visited.pop()
        seen.remove(n)
        if seen==set():
            separators.append(n)
        for req_n in get_deps(n):
            seen.add(req_n)
            dict_nb_usages[req_n]-=1
            if dict_nb_usages[req_n]==0:
                to_be_visited.append(req_n)
    separators.reverse()
    return separators

# ==========================

