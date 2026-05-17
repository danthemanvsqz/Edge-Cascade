# Validation DSL for cascade log answers.
#
# Grammar (the whole language — 2 constructs):
#   when <symbol>     start a block; it runs when an answer's code defines <symbol>
#   assert <expr>     a Python expression eval'd in that answer's namespace;
#                     must be truthy. Lines/blank/# are ignored. Indent optional.
#
# Injected helpers available in every assert: approx, sorts_like, is_avl

when add_numbers
  assert add_numbers(3, 5) == 8
  assert add_numbers(-2, 5) == 3
  assert approx(add_numbers(2.5, 0.5), 3.0)

when merge_sort
  assert sorts_like(merge_sort)

when AVLTree
  assert is_avl(AVLTree)

when dijkstra
  assert drone_ok(dijkstra) :: dijkstra(graph, start) must compute shortest-path cost on a directed weighted graph given as {node: {neighbor: weight}}. For graph {'A':{'B':4,'C':2},'B':{'C':1,'D':5},'C':{'D':8,'E':10},'D':{'E':2}} the minimum cost from 'A' to 'E' must equal 11 (path A->B->D->E). It must NOT raise KeyError for sink nodes like 'E' that have no outgoing edges (initialise distances for every node, including those only appearing as neighbours).
