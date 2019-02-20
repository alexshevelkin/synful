import logging

from pymongo import MongoClient, ASCENDING, TEXT

logger = logging.getLogger(__name__)


class NeuronDatabase(object):
    """" Neuron pymongo database interface"""

    def __init__(self, db_name, db_host='localhost', db_col_name='default', mode='r'):
        self.db_name = db_name
        self.db_host = db_host
        self.db_col = db_col_name
        self.mode = mode
        self.client = MongoClient(host=db_host)
        self.database = self.client[db_name]

        if mode == 'w':
            edge_col_name = db_col_name + '.edges'
            nodes_col_name = db_col_name + '.nodes'
            self.database.drop_collection(edge_col_name)
            self.database.drop_collection(nodes_col_name)
            logger.debug('overwriting collection %s' % db_col_name)

        self.collection = self.database[db_col_name]
        self.nodes = self.collection['nodes']
        self.edges = self.collection['edges']

        if mode == 'w':
            self.nodes.create_index(
                [
                    ('z', ASCENDING),
                    ('y', ASCENDING),
                    ('x', ASCENDING)
                ],
                name='position')

            self.nodes.create_index(
                [
                    ('id', ASCENDING)
                ],
                name='id', unique=True)

            self.nodes.create_index(
                [
                    ('neuron_id', ASCENDING)
                ],
                name='neuron_id')

            self.nodes.create_index(
                [
                    ('type', TEXT)
                ],
                name='type')

            self.edges.create_index(
                [
                    ('source', ASCENDING),
                    ('target', ASCENDING)
                ],
                name='incident')

    def write_nodes(self, nodes, roi=None):
        """Write nodes to database.

        Args:
            nodes (``list`` of ``dict``):
                List of nodes (``dict``) to be written to database. Dictionary entries are id, neuron_id, position
                and optionally type.

            roi (`daisy.Roi`):
                Only write nodes that are within given roi.

        """
        if self.mode == 'r':
            raise RuntimeError("trying to write to read-only DB")

        if roi is not None:
            ori_length = len(nodes)
            nodes = [
                n
                for n in nodes
                if roi.contains(n['position'])
                ]
            logger.debug('filtered out %i because not in roi %s' % (ori_length - len(nodes), roi))

        if len(nodes) == 0:
            logger.debug("No nodes to write.")
            return

        nodes_db = []
        for n in nodes:
            node_dic = {
                'z': int(float(n['position'][0])),  # Direct str (if float) to int conversion throws error
                'y': int(float(n['position'][1])),
                'x': int(float(n['position'][2])),
                'neuron_id': n['neuron_id'],
                'id': n['id']}
            if 'type' in n:
                node_dic.update({'type': n['type']})
            nodes_db.append(node_dic)

        logger.debug("Insert %d nodes" % len(nodes_db))

        self.nodes.insert_many(nodes_db)

    def write_edges(self, edges, nodes=None, roi=None):
        """ Write edges to database.

        Args:
            edges (``list`` of ``dict``):
                List of dicts with 'source', 'target'

            nodes (``list`` of ``dict``, optional):
                List with dicts with ``id`` and ``position`` to enable selection of edges within roi.

            roi (``daisy.Roi``, optional):
                If given, restrict writing to edges with ``source`` inside
                ``roi``.
        """

        if self.mode == 'r':
            raise RuntimeError("trying to write to read-only DB")

        if roi is not None:
            assert nodes is not None, (
                "roi given, but no nodes to check for inclusion")

            node_positions = {
                node['id']: node['position']
                for node in nodes
                }

            edges = [
                e
                for e in edges
                if roi.contains(node_positions[e['source']])
                ]

        if len(edges) == 0:
            logger.debug("No edges to write.")
            return

        logger.debug("Insert %d edges" % len(edges))

        self.edges.insert_many(edges)

    def read_nodes(self, roi=None):
        """ Read nodes from database.

        Args:
            roi (``daisy.Roi``, optional):
                If given, restrict reading nodes to ROI. If not given, all nodes are read.
        Returns:
            ``list`` of ``dict``: List of nodes (dic) with id, position, node_id and type (optional).
        """

        if roi is None:
            logger.debug("No roi provided, querying all nodes in database")
            nodes = self.nodes.find()
        else:
            logger.debug("Querying nodes in %s", roi)
            bz, by, bx = roi.get_begin()
            ez, ey, ex = roi.get_end()
            nodes = self.nodes.find(
                {
                    'z': {'$gte': bz, '$lt': ez},  # MongoDB command greater than equal.
                    'y': {'$gte': by, '$lt': ey},
                    'x': {'$gte': bx, '$lt': ex}
                })

        return self.__pymongonodes_to_dic(nodes)

    def read_nodes_based_on_edges(self, edges):
        """ Read nodes from database based on provided edges.

        Args:
            edges (``list`` of ``dict``):
                If given, restrict reading nodes to ROI. If not given, all nodes are read.

        Returns:
            ``list`` of ``dict``: List of nodes (dic) with id, position, node_id and type (optional).
        """
        nodes = [edge['source'] for edge in edges]
        nodes.extend([edge['target'] for edge in edges])
        nodes = self.nodes.find({
            'id': {'$in': nodes}
        })
        return self.__pymongonodes_to_dic(nodes)

    def __pymongonodes_to_dic(self, nodes):
        node_list = []
        for n in nodes:
            all_keys = list(n.keys())
            all_keys.remove('id')
            all_keys.remove('z')
            all_keys.remove('x')
            all_keys.remove('y')
            all_keys.remove('_id')  # dbmongo identifier not needed
            node_dic = {
                'id': n['id'],
                'position': (n['z'], n['y'], n['x'])
            }
            node_dic.update({key: n[key] for key in all_keys})
            node_list.append(node_dic)

        return node_list

    def read_neuron(self, neuron_id):
        """Read entire neuron (nodes and edges) from database.

        Args:
            neuron_id (int):
                All nodes with this neuron_id are read

        Returns:
            nodes: list of nodes
            edges: list of edges
        """

        nodes = self.nodes.find({'neuron_id': neuron_id})
        nodes = self.__pymongonodes_to_dic(nodes)
        node_ids = [n['id'] for n in nodes]
        logger.debug('neuron %i has %i nodes' % (neuron_id, len(node_ids)))
        edges = self.read_edges(source_ids=node_ids)
        edges.extend(self.read_edges(target_ids=node_ids))
        return nodes, edges



    def read_edges(self, roi=None, source_ids=None, target_ids=None):
        """Read edges from database.

        Args:
            roi (``daisy.Roi``, optional):
                If given, restrict reading edges with ``source`` inside
                ``roi``.

            source_ids (``list``):
                If given, restrict reading edges where edge source is in source_ids.

            target_ids (``list``):
                If given, restrict reading edges where edge target is in target_ids.

        Returns:
            list of dict: list of edges.
        """

        assert roi is None or source_ids is None, ('Roi and source_ids both provided, unclear what to query')
        assert roi is not None or source_ids is not None or target_ids is not None, (
            'Neither roi nor source_ids provided, unclear what to query')

        if roi is not None:
            nodes = self.read_nodes(roi)
            node_ids = list([n['id'] for n in nodes])
            logger.debug('read %i nodes in roi %s' % (len(nodes), roi))
        elif source_ids is not None:
            node_ids = source_ids
        else:
            node_ids = target_ids

        edges = []

        query_size = 128
        keyword = 'source'
        if target_ids is not None:
            keyword = 'target'
        for i in range(0, len(node_ids), query_size):
            edges += list(self.edges.find({
                keyword: {'$in': node_ids[i:i + query_size]}
            }))

        return edges


class DAGDatabase(object):
    """" General Graph Database interface to store nodes and edges"""

    def __init__(self, db_name, db_host='localhost', db_col_name='default',
                 mode='r'):

        self.db_name = db_name
        self.db_host = db_host
        self.db_col = db_col_name
        self.mode = mode
        self.client = MongoClient(host=db_host)
        self.database = self.client[db_name]

        if mode == 'w':
            edge_col_name = db_col_name + '.edges'
            nodes_col_name = db_col_name + '.nodes'
            config_col_name = db_col_name + '.config'
            self.database.drop_collection(edge_col_name)
            self.database.drop_collection(nodes_col_name)
            self.database.drop_collection(config_col_name)
            logger.debug('overwriting collection %s' % db_col_name)

        self.collection = self.database[db_col_name]
        self.nodes = self.collection['nodes']
        self.edges = self.collection['edges']

        if mode == 'w':
            self.nodes.create_index(
                [
                    ('z', ASCENDING),
                    ('y', ASCENDING),
                    ('x', ASCENDING)
                ],
                name='position')

            self.nodes.create_index(
                [
                    ('id', ASCENDING)
                ],
                name='id')
            self.nodes.create_index(
                [
                    ('score', ASCENDING)
                ],
                name='score')

            self.edges.create_index(
                [
                    ('source', ASCENDING),
                    ('target', ASCENDING)
                ],
                name='incident')

    def write_nodes(self, nodes, roi=None):
        """Write nodes to database.

        Args:
            nodes (``list`` of ``dict``):
                List of nodes (``dict``) to be written to database. Dictionary entries are id, neuron_id, position
                and optionally type.

            roi (`daisy.Roi`):
                Only write nodes that are within given roi.

        """

        if self.mode == 'r':
            raise RuntimeError("trying to write to read-only DB")

        if roi is not None:
            nodes = [
                n
                for n in nodes
                if roi.contains(n['position'])
            ]

        if len(nodes) == 0:
            logger.debug("No nodes to write.")
            return

        nodes_db = []
        for n in nodes:
            node_keys = n.keys()
            node_keys.remove('position')
            node_dic = {
                'z': int(float(n['position'][0])),
                # Direct str (if flaot) to int conversion throws error
                'y': int(float(n['position'][1])),
                'x': int(float(n['position'][2])),
            }
            node_dic.update({key: n[key] for key in node_keys})
            nodes_db.append(node_dic)

        logger.debug("Insert %d nodes" % len(nodes_db))

        self.nodes.insert_many(nodes_db)

    def read_nodes_based_on_edges(self, edges):
        '''Return a list of dictionaries with ``id``, ``position``, and
        ``score`` for each source and target node of provided edges.
        Args:
            edges (``list``): List of edges represented with dictionaries and
            'source' and 'target' as keys. Finds all source nodes from provided edges.
        '''
        nodes = [edge['source'] for edge in edges]
        nodes.extend([edge['target'] for edge in edges])
        nodes = self.nodes.find({
            'id': {'$in': nodes}
        })
        return self.__pymongonodes_to_dic(nodes)

    def read_nodes_based_on_ids(self, ids):
        '''Return a list of dictionaries with ``id``, ``position``, and
        ``score`` for each provided node id.
                Args:
            ids (``list``): List of node ids.
        '''
        nodes = self.nodes.find({
            'id': {'$in': ids}
        })
        return self.__pymongonodes_to_dic(nodes)

    def __pymongonodes_to_dic(self, nodes):
        node_list = []
        for n in nodes:
            all_keys = list(n.keys())
            all_keys.remove('id')
            all_keys.remove('z')
            all_keys.remove('x')
            all_keys.remove('y')
            all_keys.remove('_id')  # dbmongo identifier not needed
            node_dic = {
                'id': n['id'],
                'position': (n['z'], n['y'], n['x'])
            }
            node_dic.update({key: n[key] for key in all_keys})
            node_list.append(node_dic)
        return node_list

    def read_nodes(self, roi=None):
        '''Return a list of dictionaries with ``id``, ``position``, and
        ``score`` for each node in ``roi``.
        Args:
            roi (```daisy.Roi``, optional):
                If given, restrict reading nodes to ROI.
        '''

        if roi is None:
            logger.debug("No roi provided, querying all nodes in database")
            nodes = self.nodes.find()
        else:
            logger.debug("Querying nodes in %s", roi)
            bz, by, bx = roi.get_begin()
            ez, ey, ex = roi.get_end()
            nodes = self.nodes.find(
                {
                    'z': {'$gte': bz, '$lt': ez},
                    # MongoDB command greater than equal.
                    'y': {'$gte': by, '$lt': ey},
                    'x': {'$gte': bx, '$lt': ex}
                })

        return self.__pymongonodes_to_dic(nodes)

    def write_edges(self, edges):
        '''Write edges to the DB.
        Args:
            edges (``list``):
                List of dicts with 'source', 'target', and
                'score'.
        '''

        if self.mode == 'r':
            raise RuntimeError("trying to write to read-only DB")

        if len(edges) == 0:
            logger.debug("No edges to write.")
            return

        logger.debug("Insert %d edges" % len(edges))

        self.edges.insert_many(edges)

    def read_edges(self, roi=None, source_ids=None, target_ids=None):
        '''Read edges from DB.
        Args:
            roi (``daisy.Roi``, optional):
                If given, restrict reading edges with ``source`` inside
                ``roi``.
            source_ids (list):
                If given, restrict reading edges with source nodes provided.
            target_ids (list):
                If given, restrict reading edges with target nodes provided.
        '''

        assert roi is None or source_ids is None, (
            'Roi and source_ids both provided, unclear what to query')
        assert roi is not None or source_ids is not None or target_ids is not None, (
            'Neither roi nor source_ids provided, unclear what to query')

        if roi is not None:
            nodes = self.read_nodes(roi)
            # nodes.batch_size(1000)
            node_ids = list([n['id'] for n in nodes])
            logger.debug('read %i nodes in roi %s' % (len(nodes), roi))
        elif source_ids is not None:
            node_ids = source_ids
        else:
            node_ids = target_ids

        edges = []

        query_size = 128
        keyword = 'source'
        if target_ids is not None:
            keyword = 'target'
        for i in range(0, len(node_ids), query_size):
            edges += list(self.edges.find({
                keyword: {'$in': node_ids[i:i + query_size]}
            }))

        return edges
