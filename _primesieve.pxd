from libc.stdint cimport uint64_t

cdef extern from "primesieve/iterator.h":
    ctypedef struct primesieve_iterator:
        int is_error
    void primesieve_init(primesieve_iterator*)
    void primesieve_free_iterator(primesieve_iterator*)
    void primesieve_jump_to(primesieve_iterator*, uint64_t, uint64_t)
    uint64_t primesieve_next_prime(primesieve_iterator*)
