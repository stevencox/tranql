import copy
import json
import logging
import requests
import requests_cache
import sys
import traceback
from collections import defaultdict
from tranql.concept import ConceptModel
from tranql.util import Concept
from tranql.util import JSONKit
from tranql.tranql_schema import Schema
from tranql.exception import ServiceInvocationError
from tranql.exception import UndefinedVariableError
from tranql.exception import UnableToGenerateQuestionError
from tranql.exception import MalformedResponseError
from tranql.exception import IllegalConceptIdentifierError

logger = logging.getLogger (__name__)

class Bionames:
    """ Resolve natural language names to ontology identifiers. """
    def __init__(self):
        """ Initialize the operator. """
        self.url = "https://bionames.renci.org/lookup/{input}/{type}/"
    
    def get_ids (self, name, type_name):
        url = self.url.format (**{
            "input" : name,
            "type"  : type_name
        })
        result = None
        response = requests.get(
            url = url,
            headers = {
                'accept': 'application/json'
            })
        if response.status_code == 200 or response.status_code == 202:
            result = response.json ()
        else:
            raise ServiceInvocationError (response.text)
        return result
    
class Statement:
    """ The interface contract for a statement. """
    def execute (self, interpreter, context={}):
        pass

    def resolve_backplane_url(self, url, interpreter):
        result = url
        if url.startswith ('/'):
            backplane = interpreter.context.resolve_arg ("$backplane")
            result =  f"{backplane}{url}"
        return result    

    def message (self, q_nodes=[], q_edges=[], k_nodes=[], k_edges=[], options={}):
        """ Generate the frame of a question. """
        return {
            "question_graph": {
                "edges": q_edges,
                "nodes": q_nodes
            },
            "knowledge_graph" : {
                "nodes" : k_nodes,
                "edges" : k_edges
            },
            "knowledge_maps" : [
                {}
            ],
            "options" : options
        }
    
    def request (self, url, message):
        """ Make a web request to a service (url) posting a message. """
        logger.debug (f"request> {json.dumps(message, indent=2)}")
        response = {}
        try:
            http_response = requests.post (
                url = url,
                json = message,
                headers = {
                    'accept': 'application/json'
                })
            """ Check status and handle response. """
            if http_response.status_code == 200 or http_response.status_code == 202:
                response = http_response.json ()
                logging.debug (f"{json.dumps(response, indent=2)}")
            else:
                logger.error (f"error {http_response.status_code} processing request: {message}")
                logger.error (http_response.text)
        except:
            logger.error (f"error performing request: {json.dumps(message, indent=2)} to url: {url}")
            traceback.print_exc ()
        return response
    
class SetStatement(Statement):
    """ Model the set statement's semantics and variants. """
    def __init__(self, variable, value=None, jsonpath_query=None):
        """ Model the various forms of assignment supported. """
        self.variable = variable
        self.value = value
        self.jsonpath_query = jsonpath_query
        self.jsonkit = JSONKit ()
    def execute (self, interpreter, context={}):
        logger.debug (f"set-statement: {self.variable}={self.value}")
        return_val = None
        if self.value:
            logger.debug (f"exec-set-statement(explicit-value): {self}")
            interpreter.context.set (self.variable, self.value)
            return_val = self.value
        elif 'result' in context:
            result = context['result']
            if self.jsonpath_query is not None:
                logger.debug (f"exec-set-statement(jsonpath): {self}")
                value = self.jsonkit.select (
                    query=self.jsonpath_query,
                    graph=result)
                if len(value) == 0:
                    print (f"Got empty set for query {self.jsonpath_query} on " +
                           f"object {json.dumps(result, indent=2)}")
                interpreter.context.set (
                    self.variable,
                    value)
                return_val = value
            else:
                logger.debug (f"exec-set-statement(result): {self}")
                interpreter.context.set (self.variable, result)
                return_val = result
        return return_val

    def __repr__(self):
        result = f"SET {self.variable}"
        if self.jsonpath_query is not None:
            result = f"SET {self.jsonpath_query} AS {self.variable}"
        elif self.value is not None:
            result = f"{result} = {self.value}"
        return result

class CreateGraphStatement(Statement):
    """ Create a graph, sending it to a sink. """
    def __init__(self, graph, service, name):
        """ Construct a graph creation statement. """
        self.graph = graph
        self.service = service
        self.name = name
    def __repr__(self):
        return f"CREATE GRAPH {self.graph} AT {self.service} AS {self.name}"
    def execute (self, interpreter):
        """ Execute the statement. """
        self.service = self.resolve_backplane_url(self.service,
                                                  interpreter)
        graph = interpreter.context.resolve_arg (self.graph)
        logger.debug (f"------- {type(graph).__name__}")
        logger.debug (f"--- create graph {self.service} graph-> {json.dumps(graph, indent=2)}")
        response = None
        #with requests_cache.disabled ():
        response = self.request (url=self.service,
                                 message=graph)
        interpreter.context.set (self.name, response)
        return response
    
def synonymize(nodetype,identifier):
    robokop_server = 'robokopdb2.renci.org'
    robokop_server = 'robokop.renci.org'
    url=f'http://{robokop_server}/api/synonymize/{identifier}/{nodetype}/'
    url=f'http://{robokop_server}:6010/api/synonymize/{identifier}/{nodetype}/'
    response = requests.post(url)
    logger.debug (f'Return Status: {response.status_code}' )
    if response.status_code == 200:
        return response.json()
    return []

class SelectStatement(Statement):
    """
    Model a select statement.
    This entails all capabilities from specifying a knowledge path, service to invoke, constraints, and handoff.
    """
    def __init__(self):
        """ Initialize a new select statement. """
        self.query = Query ()
        self.service = None
        self.where = []
        self.set_statements = []
        self.id_count = 0
        self.id_namespaces = defaultdict(int)
    def __repr__(self):
        return f"SELECT {self.query} from:{self.service} where:{self.where} set:{self.set_statements}"
    
    def next_id(self, namespace="n"):
        result_id = self.id_namespaces[namespace]
        result = f"{namespace}{result_id}"
        self.id_namespaces[namespace] = result_id + 1
        return result
    def edge (self, source, target, type_name=None):
        """ Generate a question edge. """
        e = {
            "id" : f"{self.next_id(namespace='e')}",
            "source_id": source,
            "target_id": target
        }
        if type_name is not None:
            e["type"] = type_name
        return e
    def node (self, type_name, value=None):
        """ Generate a question node. """
        identifier = self.next_id ()
        n = {
            "id": f"{identifier}",
            "type": type_name
        }
        if value:
            n ['curie'] = value 
        return n

    def val(self, value, field="id"):
        """ Get the value of an object. """
        result = value
        if isinstance(value, dict) and field in value:
            result = value[field]
        return result

    def resolve_name (self, name, type_name):
        bionames = Bionames ()
        result = [ r['id'] for r in bionames.get_ids (name, type_name) ]
        #result += self.synonymize (value, type_name)
        if type_name == 'chemical_substance':
            response = requests.get (f"http://mychem.info/v1/query?q={name}").json ()
            for obj in response['hits']:
                if 'chebi' in obj:
                    result.append (obj['chebi']['id'])
        logger.debug (f"name resolution result: {name} => {result}")
        return result
    
    def expand_nodes (self, interpreter, concept):
        """ Expand variable expressions to nodes. """
        value = concept.nodes[0] if len(concept.nodes) > 0 else None
        if value and isinstance(value, str):
            if value.startswith ("$"):
                varname = value
                value = interpreter.context.resolve_arg (varname)
                print (f"resolved {varname}............... {value}")
                logger.debug (f"resolved {varname} to {value}")
                if value == None:
                    raise UndefinedVariableError (f"Undefined variable: {varname}")
                elif isinstance (value, str):
                    concept.nodes = [ value ]
                elif isinstance(value, list):
                    """ Binding multiple values to a node. """
                    concept.nodes = value
                else:
                    raise TranQLException (
                        f"Internal failure: object of unhandled type {type(value)}.")
            else:
                """ Bind a single value to a node. """
                if not ':' in value:
                    """ Deprecated. """
                    """ Bind something that's not a curie. Dynamic id lookup.
                    This is frowned upon. While it *may* be useful for prototyping and,
                    interactive exploration, it will probably be removed. """
                    logger.debug (f"performing dynamic lookup resolving {concept}={value}")
                    concept.nodes = self.resolve_name (value, concept.name)
                    logger.debug (f"resolved {value} to identifiers: {concept.nodes}")
                else:
                    """ This is a single curie. Bind it to the node. """
                    concept.nodes = [ self.node (
                        value=value,
                        type_name = concept.name) ]

    def generate_questions (self, interpreter):
        """
        Given an archetype question graph and values, generate question
        instances for each value permutation.
        """
        for index, name in enumerate(self.query.order):
            """ Convert literals into nodes in the message's question graph. """
            concept = self.query[name]
            print (f"concept---->: {concept}")
            if len(concept.nodes) > 0:
                self.expand_nodes (interpreter, concept)
                #logger.debug (f"concept--nodes: {concept.nodes}")
                concept.nodes = [
                    self.node (
                        type_name = concept.name,
                        value = self.val(v, field='curie'))
                    for v in concept.nodes
                ]
                filters = interpreter.context.resolve_arg ('$id_filters')
                if filters:
                    filters = [ f.lower () for f in filters.split(",") ]
                    concept.nodes = [
                        n for n in concept.nodes
                        if not n['curie'].split(':')[0].lower () in filters
                    ]
            else:
                """ There are no values - it's just a template for a model type. """
                concept.nodes = [ self.node (
                    type_name = concept.name) ]
            
        options = {}
        for constraint in self.where:
            logger.debug (f"manage constraint: {constraint}")
            name, op, value = constraint
            value = interpreter.context.resolve_arg (value)
            if not name in self.query:
                """
                This is not constraining a concept name in the graph query.
                So interpret it as an option to the underlying service.
                """
                options[name] = constraint[1:]
        edges = []
        questions = []
        logger.debug (f"concept order> {self.query.order}")
        for index, name in enumerate (self.query.order):
            concept = self.query[name] 
            previous = self.query.order[index-1] if index > 0 else None
            logger.debug (f"query:{self.query}")
            logger.debug (f"questions:{index} ==>> {json.dumps(questions, indent=2)}")
            if index == 0:
                """ Model the first step. """
                if len(concept.nodes) > 0:
                    """ The first concept is bound. """
                    for node in concept.nodes:
                        questions.append (self.message (
                            q_nodes = [ node ],
                            q_edges = [],
                            options = options))
                else:
                    """ No nodes specified for the first concept. """
                    questions.append (self.message (options))
            else:
                """ Not the first concept - permute relative to previous. """
                new_questions = []
                for question in questions:
                    if len(concept.nodes) > 0:
                        for node in concept.nodes:
                            """ Permute each question. """
                            nodes = copy.deepcopy (question["question_graph"]['nodes'])
                            print (f"------> {concept} {json.dumps(nodes, indent=2)}")
                            lastnode = nodes[-1]
                            nodes.append (node)
                            edges = copy.deepcopy (question["question_graph"]['edges'])
                            edge_spec = self.query.arrows[index-1]
                            if edge_spec.direction == self.query.forward_arrow:
                                edges.append (self.edge (
                                    source=lastnode['id'],
                                    target=node['id'],
                                    type_name = edge_spec.predicate))
                            else:
                                edges.append (self.edge (
                                    source=node['id'],
                                    target=lastnode['id'],
                                    type_name = edge_spec.predicate))
                            new_questions.append (self.message (
                                q_nodes = nodes,
                                options = options,
                                q_edges = edges))
                    else:
                        query_nodes = question['question_graph']['nodes']
                        question['question_graph']['nodes'].append (
                            self.node (type_name = concept.name))
                        source_id = query_nodes[-2]['id']
                        target_id = query_nodes[-1]['id']
                        question['question_graph']['edges'].append (
                            self.edge (
                                source = source_id,
                                target = target_id))
                        new_questions.append (self.message (options))
                questions = new_questions
        return questions

    def execute (self, interpreter, context={}):
        """
        Execute all statements in the abstract syntax tree.
        - Generate questions by permuting bound values.
        - Resolve the service name.
        - Execute the questions.
        """
        self.service = self.resolve_backplane_url (self.service, interpreter)
        questions = self.generate_questions (interpreter)
        if len(questions) == 0:
            raise UnableToGenerateQuestionError ("No questions generated")
        service = interpreter.context.resolve_arg (self.service)

        """ Invoke the service and store the response. """
        responses = []
        for q in questions:
            logger.debug (f"executing question {json.dumps(q, indent=2)}")
            response = self.request (service, q)
            logger.debug (f"response: {json.dumps(response, indent=2)}")
            responses.append (response)
            
        if len(responses) == 0:
            raise ServiceInvocationError (f"No responses received from {service}")
        elif len(responses) == 1:
            result = responses[0]
        elif len(responses) > 1:
            result = responses[0]
            if not 'knowledge_graph' in result:
                message = "Malformed response does not contain knowledge_graph element."
                logger.error (f"{message} svce: {service}: {json.dumps(result, indent=2)}")
                raise MalformedResponseError (message)
            nodes = result['knowledge_graph']['nodes']
            edges = result['knowledge_graph']['edges']
            nodes = { n['id'] : n for n in nodes }
            for response in responses[1:]:
                # TODO: Preserve reasoner provenance. This treats nodes as equal if
                # their ids are equal. Instead, consider merging provenance/properties.
                # Edges, we may keep distinct and whole or merge to some tbd extent.
                edges += response['knowledge_graph']['edges']
                other_nodes = response['knowledge_graph']['nodes']
                for n in other_nodes:
                    if not n['id'] in nodes:
                        nodes[n['id']] = n
            result['knowledge_graph']['nodes'] = list(nodes.values ())
        for set_statement in self.set_statements:
            logger.debug (f"{set_statement}")
            set_statement.execute (interpreter, context = { "result" : result })
        return result
    
class TranQL_AST:
    """Represent the abstract syntax tree representing the logical structure of a parsed program."""
    def __init__(self, parse_tree):
        logger.debug (f"{json.dumps(parse_tree, indent=2)}")
        """ Create an abstract syntax tree from the parser token stream. """
        self.schema = Schema ()
        self.statements = []
        self.parse_tree = parse_tree
        logger.debug (f"{json.dumps(self.parse_tree, indent=2)}")
        for index, element in enumerate(self.parse_tree):
            if isinstance (element, list):
                statement = self.remove_whitespace (element, also=["->"])
                if element[0] == 'set':
                    if len(element) == 4:
                        self.statements.append (SetStatement (
                            variable = element[1],
                            value = element[3]))
                elif isinstance(element[0], list):
                    statement = self.remove_whitespace (element[0], also=["->"])
                    command = statement[0]
                    if command == 'select':
                        self.parse_select (element)
                    elif command == 'create':
                        self.parse_create (element)

    def parse_create(self, element):
        """ Parse a create graph statement. """
        element = self.remove_whitespace (element)
        self.statements.append (
            CreateGraphStatement (
                graph = element[0][2],
                service = element[1][1],
                name = element[2][1]))
        logger.debug (f"--parse_create(): {self.statements[-1]}")
        
    def remove_whitespace (self, group, also=[]):
        """
        Delete spurious items in a statement.
        TODO: Look at enhancing the parser to provider cleaner input in the first place.
        """
        return [ x for x in group
                 if not isinstance(x, str) or
                 (not x.isspace () and not x in also) ]
        
    def parse_select (self, statement):
        """ Parse a select statement. """
        select = SelectStatement ()
        for e in statement:
            if self.is_command (e):
                e = self.remove_whitespace (e)
                command = e[0]
                if command == 'select':
                    for token in e[1:]:
                        select.query.add (token)
                if command == 'from':
                    select.service = e[1][0]
                elif command == 'where':
                    for condition in e[1:]:
                        if isinstance(condition, list) and len(condition) == 3:
                            select.where.append (condition)
                            var, op, val = condition
                            if var in select.query and op == '=':
                                select.query[var].nodes.append (val)
                            else:
                                select.where.append ([ var, op, val ])
                elif command == 'set':
                    element = e[1]
                    if len(element) == 3:
                        select.set_statements.append (
                            SetStatement (variable=element[2],
                                          value=None,
                                          jsonpath_query=element[0]))
                    elif len(element) == 1:
                        select.set_statements.append (
                            SetStatement (variable=element[0]))
        self.statements.append (select)

    def is_command (self, e):
        """ Is this structured like a command? """
        return isinstance(e, list) and len(e) > 0
    
    def __repr__(self):
        return json.dumps(self.parse_tree)

class Edge:
    def __init__(self, direction, predicate=None):
        self.direction = direction
        self.predicate = predicate
    def __repr__(self):
        return f"edge[dir:{self.direction},pred:{self.predicate}]"
    
class Query:
    """ Model a query. 
    TODO:
       - Model queries with arrows in both diretions.
       - Model predicates
       - Model arbitrary shaped graphs.
    """

    """ Arrows in the query. """
    back_arrow = "<-"
    forward_arrow = "->"

    """ The biolink model. Will use for query validation. """
    concept_model = ConceptModel ("biolink-model")
    
    def __init__(self):
        self.order = []
        self.arrows = []
        self.concepts = {}
        
    def add(self, key):
        """ Add a token in the question graph to this query object. """
        if key == self.forward_arrow or key == self.back_arrow:
            """ It's a forward arrow, no predicate. """
            self.arrows.append (Edge(direction=key))
        elif isinstance(key, list) and len(key) == 3:
            if key[2].endswith(self.forward_arrow):
                self.arrows.append (Edge(direction=self.forward_arrow,
                                         predicate=key[1]))
            elif key[0].startswith(self.back_arrow):
                self.arrows.append (Edge(direction=self.back_arrow,
                                         predicate=key[1]))
        else:
            """ It's a concept identifier, potentially named. """
            concept = key
            name = key
            if ':' in key:
                if key.count (':') > 1:
                    raise IllegalConceptIdentifierError (f"Illegal concept id: {key}")
                name, concept = key.split (':')
            self.order.append (name)
            assert self.concept_model.get (concept) != None
            self.concepts[name] = Concept (concept)
    def __getitem__(self, key):
        return self.concepts [key]
    def __setitem__(self, key, value):
        raise ValueError ("Not implemented")
    def __delitem__ (self, key):
        del self.concepts[key]
    def __contains__ (self, key):
        return key in self.concepts
    def __repr__(self):
        return f"{self.concepts} | {self.arrows}"