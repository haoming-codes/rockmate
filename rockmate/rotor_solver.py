# ==========================
# modified version of rotor algo
# contains RK_Sequence builder -> depends on RK_Chain
# based on rotor/algorithms/persistent.py
# ==========================

from .utils import *
from .def_chain import RK_Chain
from .def_sequence import *
from .def_code import RK_Function

# ==========================
# ==== DYNAMIC PROGRAM =====
# ==========================

def solve_dp_functionnal(chain : RK_Chain, mmax):
    """Returns the optimal table:
    Opt[m][lmin][lmax] : int matrix
        with lmin = 0...chain.length
        and  lmax = lmin...chain.length (lmax is not included)
        and  m    = 0...mmax
    What[m][lmin][lmax] is :
        (True, k) if the optimal choice is a chain chkpt
        -> ie F_e this block with solution k
        (False,j) if the optimal choice is a leaf  chkpt
        -> ie F_c and then F_n (j-1) blocks
    """

    ln      = chain.ln
    fw      = chain.fw
    bw      = chain.bw
    cw      = chain.cw
    cbw     = chain.cbw
    fwd_tmp = chain.fwd_tmp
    bwd_tmp = chain.bwd_tmp
    ff_fwd_tmp = chain.ff_fwd_tmp
    ff_fw   = chain.ff_fw
    nb_sol  = chain.nb_sol

    opt = dict()
    what = dict()
    def opt_add(m,a,b,time):
        if not m in opt: opt[m] = dict()
        if not a in opt[m]: opt[m][a] = dict()
        opt[m][a][b] = time
    def what_add(m,a,b,time):
        if not m in what: what[m] = dict()
        if not a in what[m]: what[m][a] = dict()
        what[m][a][b] = time
    # -> Last one is a dict because its indices go from i to l. 
    # -> Renumbering will wait for C implementation

    # -- Initialize borders of the tables for lmax-lmin = 0 --
    def case_d_0(m,i):
        possibilities = []
        for k in range(nb_sol[i]):
            limit = max(cw[i+1] + cbw[i+1][k] + fwd_tmp[i][k],
                        cw[i]+ cw[i+1] + cbw[i+1][k] + bwd_tmp[i][k])
            if m >= limit:
                possibilities.append((k,fw[i][k] + bw[i][k]))
        if possibilities == []:
            opt_add(m,i,i,float("inf"))
        else:
            best_sol = min(possibilities, key = lambda t: t[1])
            opt_add(m,i,i,best_sol[1])
            what_add(m,i,i,(True,best_sol[0]))
        return opt[m][i][i]

    # -- dynamic program --
    nb_call = 0
    def solve_aux(m,a,b):
        if ((m not in opt)
            or (a not in opt[m])
            or (b not in opt[m][a])):
            nonlocal nb_call ; nb_call += 1
            if a==b:
                return case_d_0(m,a)
            # lmin = a ; lmax = b
            mmin = cw[b+1] + cw[a+1] + ff_fwd_tmp[a]
            if b > a+1:
                mmin = max(mmin,
                    cw[b+1] + max(cw[j]+cw[j+1]+ff_fwd_tmp[j]
                    for j in range(a+1, b)))
            if m < mmin:
                opt_add(m,a,b,float("inf"))
            else:
                # -- Solution 1 --
                sols_later = [
                    (j,(sum(ff_fw[a:j])
                        + solve_aux(m-cw[j],j,b)
                        + solve_aux(m,a,j-1)))
                    for j in range(a+1, b+1)
                    if m >= cw[j] ]
                if sols_later:
                    best_later = min(sols_later, key = lambda t: t[1])
                else: best_later = None

                # -- Solution 2 --
                # -> we can no longer use opt[i][i] because the cbw
                # -> now depend on the F_e chosen. 
                sols_now = []
                for k in range(nb_sol[a]):
                    mem_f = cw[a+1] + cbw[a+1][k] + fwd_tmp[a][k]
                    mem_b = cw[a]+cw[a+1]+cbw[a+1][k]+bwd_tmp[a][k]
                    limit = max(mem_f,mem_b)
                    if m >= limit:
                        time = fw[a][k] + bw[a][k]
                        time += solve_aux(m-cbw[a+1][k],a+1,b)
                        sols_now.append((k,time))
                if sols_now:
                    best_now = min(sols_now, key = lambda t: t[1])
                else: best_now = None

                # -- best of 1 and 2 --
                if best_later is None and best_now is None:
                    opt_add(m,a,b,float("inf"))
                elif (best_later is None
                    or (best_now is not None
                    and best_now[1]<best_later[1])):
                    opt_add(m,a,b,best_now[1])
                    what_add(m,a,b,(True, best_now[0]))
                else:
                    opt_add(m,a,b,best_later[1])
                    what_add(m,a,b,(False, best_later[0]))
        return opt[m][a][b]

    solve_aux(mmax,0,ln)

    print_debug(f"Nb calls : {nb_call}")
    return (opt,what)


def solve_dp_iterative(chain : RK_Chain, mmax):
    """Returns the optimal table:
    Opt[m][lmin][lmax] : int matrix
        with lmin = 0...chain.length
        and  lmax = lmin...chain.length (lmax is not included)
        and  m    = 0...mmax
    What[m][lmin][lmax] is :
        (True, k) if the optimal choice is a chain chkpt
        -> ie F_e this block with solution k
        (False,j) if the optimal choice is a leaf  chkpt
        -> ie F_c and then F_n (j-1) blocks
    """

    ln      = chain.ln
    fw      = chain.fw
    bw      = chain.bw
    cw      = chain.cw
    cbw     = chain.cbw
    fwd_tmp = chain.fwd_tmp
    bwd_tmp = chain.bwd_tmp
    ff_fwd_tmp = chain.ff_fwd_tmp
    ff_fw   = chain.ff_fw
    nb_sol  = chain.nb_sol

    opt =  [[{} for _ in range(ln+1)] for _ in range(mmax + 1)]
    what = [[{} for _ in range(ln+1)] for _ in range(mmax + 1)]
    # -> Last one is a dict because its indices go from i to l. 
    # -> Renumbering will wait for C implementation

    # -- Initialize borders of the tables for lmax-lmin = 0 --
    for m in range(mmax + 1):
        for i in range(ln + 1):
            # lmax = lmin = i
            possibilities = []
            for k in range(nb_sol[i]):
                limit = max(cw[i+1] + cbw[i+1][k] + fwd_tmp[i][k],
                            cw[i]+ cw[i+1] + cbw[i+1][k] + bwd_tmp[i][k])
                if m >= limit:
                    possibilities.append((k,fw[i][k] + bw[i][k]))
            if possibilities == []:
                opt[m][i][i] = float("inf")
            else:
                best_sol = min(possibilities, key = lambda t: t[1])
                opt[m][i][i] = best_sol[1]
                what[m][i][i] = (True,best_sol[0])

    # -- dynamic program --
    for m in range(mmax + 1):
        for d in range(1, ln + 1):
            for a in range(ln + 1 - d):
                b = a + d
                # lmin = a ; lmax = b
                mmin = cw[b+1] + cw[a+1] + ff_fwd_tmp[a]
                if b > a+1:
                    mmin = max(mmin,
                        cw[b+1] + max(cw[j]+cw[j+1]+ff_fwd_tmp[j]
                        for j in range(a+1, b)))
                if m < mmin:
                    opt[m][a][b] = float("inf")
                else:
                    # -- Solution 1 --
                    sols_later = [
                        (j,(sum(ff_fw[a:j])
                            + opt[m-cw[j]][j][b]
                            + opt[m][a][j-1]))
                        for j in range(a+1, b+1)
                        if m >= cw[j] ]
                    if sols_later:
                        best_later = min(sols_later, key = lambda t: t[1])
                    else: best_later = None

                    # -- Solution 2 --
                    # -> we can no longer use opt[i][i] because the cbw
                    # -> now depend on the F_e chosen. 
                    sols_now = []
                    for k in range(nb_sol[a]):
                        mem_f = cw[a+1] + cbw[a+1][k] + fwd_tmp[a][k]
                        mem_b = cw[a]+cw[a+1]+cbw[a+1][k]+bwd_tmp[a][k]
                        limit = max(mem_f,mem_b)
                        if m >= limit:
                            time = fw[a][k] + bw[a][k]
                            time += opt[m-cbw[a+1][k]][a+1][b]
                            sols_now.append((k,time))
                    if sols_now:
                        best_now = min(sols_now, key = lambda t: t[1])
                    else: best_now = None

                    # -- best of 1 and 2 --
                    if best_later is None and best_now is None:
                        opt[m][a][b] = float("inf")
                    elif (best_later is None
                        or (best_now is not None
                        and best_now[1]<best_later[1])):
                        opt[m][a][b] = best_now[1]
                        what[m][a][b] = (True, best_now[0])
                    else:
                        opt[m][a][b] = best_later[1]
                        what[m][a][b] = (False, best_later[0])

    return (opt,what)

# ==========================



# ==========================
# ==== SEQUENCE BUILDER ====
# ==========================

def seq_builder(chain : RK_Chain, memory_limit):
    # returns :
    # - the optimal sequence of computation using mem-persistent algo
    # - "Functions" : RK_Function list -> to exec the code
    chain.build_rotor_chain()
    mmax = memory_limit - chain.cw[0]
    opt, what = solve_dp_functionnal(chain,mmax)
    functions = []
    for block in chain.body:
        functions.append(RK_Function(
            code_fe  = None,
            code_fn  = block.code_fn,
            code_fc  = block.code_fc,
            code_bwd = None))

    # ~~~~~~~~~~~~~~~~~~
    def seq_builder_rec(lmin, lmax, cmem):
        seq = RK_Sequence()
        if lmin > lmax: return seq
        if cmem <= 0:
            raise ValueError(
                f"Can't process a chain with neg mem {cmem}")
        if opt[cmem][lmin][lmax] == float("inf"):
            raise ValueError(
                f"Can't process this chain from index "\
                f"{lmin} to {lmax} with memory {cmem}")

        if lmin == chain.ln:
            seq.insert(SeqLoss())
            return seq

        w = what[cmem][lmin][lmax]
        # -- Solution 1 --
        if w[0]:
            k = w[1]
            sol = chain.body[lmin].sols[k]
            functions[lmin].code_fe  = sol.code_fwd
            functions[lmin].code_bwd = sol.code_bwd
            seq.insert(SeqBlockFe(lmin,sol.code_fwd))
            seq.insert_seq(
                seq_builder_rec(lmin+1,lmax,cmem-chain.cbw[lmin+1][k]))
            seq.insert(SeqBlockBwd(lmin,sol.code_bwd))

        # -- Solution 1 --
        else:
            j = w[1]
            seq.insert(SeqBlockFc(lmin,functions[lmin].code_fc))
            for k in range(lmin+1,j):
                #seq.insert(SeqBlockFn(lmin,functions[lmin].code_fn))
                seq.insert(SeqBlockFn(k,functions[k].code_fn))
            seq.insert_seq(seq_builder_rec(j,lmax,cmem-chain.cw[j]))
            seq.insert_seq(seq_builder_rec(lmin,j-1,cmem))
        return seq
    # ~~~~~~~~~~~~~~~~~~

    seq = seq_builder_rec(0, chain.ln, mmax)
    return seq,functions

# ==========================

