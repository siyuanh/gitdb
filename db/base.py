"""Contains implementations of database retrieveing objects"""
from gitdb.util import (
		pool,
		join, 
		LazyMixin 
	)

from gitdb.exc import BadObject

from async import (
		ChannelThreadTask
	)

from itertools import chain


__all__ = ('ObjectDBR', 'ObjectDBW', 'FileDBBase', 'CompoundDB', 'CachingDB')


class ObjectDBR(object):
	"""Defines an interface for object database lookup.
	Objects are identified either by their 20 byte bin sha"""
	
	def __contains__(self, sha):
		return self.has_obj
	
	#{ Query Interface 
	def has_object(self, sha):
		"""
		:return: True if the object identified by the given 20 bytes
			binary sha is contained in the database"""
		raise NotImplementedError("To be implemented in subclass")
		
	def has_object_async(self, reader):
		"""Return a reader yielding information about the membership of objects
		as identified by shas
		:param reader: Reader yielding 20 byte shas.
		:return: async.Reader yielding tuples of (sha, bool) pairs which indicate
			whether the given sha exists in the database or not"""
		task = ChannelThreadTask(reader, str(self.has_object_async), lambda sha: (sha, self.has_object(sha)))
		return pool.add_task(task) 
		
	def info(self, sha):
		""" :return: OInfo instance
		:param sha: bytes binary sha
		:raise BadObject:"""
		raise NotImplementedError("To be implemented in subclass")
		
	def info_async(self, reader):
		"""Retrieve information of a multitude of objects asynchronously
		:param reader: Channel yielding the sha's of the objects of interest
		:return: async.Reader yielding OInfo|InvalidOInfo, in any order"""
		task = ChannelThreadTask(reader, str(self.info_async), self.info)
		return pool.add_task(task)
		
	def stream(self, sha):
		""":return: OStream instance
		:param sha: 20 bytes binary sha
		:raise BadObject:"""
		raise NotImplementedError("To be implemented in subclass")
		
	def stream_async(self, reader):
		"""Retrieve the OStream of multiple objects
		:param reader: see ``info``
		:param max_threads: see ``ObjectDBW.store``
		:return: async.Reader yielding OStream|InvalidOStream instances in any order
		:note: depending on the system configuration, it might not be possible to 
			read all OStreams at once. Instead, read them individually using reader.read(x)
			where x is small enough."""
		# base implementation just uses the stream method repeatedly
		task = ChannelThreadTask(reader, str(self.stream_async), self.stream)
		return pool.add_task(task)
	
	def size(self):
		""":return: amount of objects in this database"""
		raise NotImplementedError()
		
	def sha_iter(self):
		"""Return iterator yielding 20 byte shas for all objects in this data base"""
		raise NotImplementedError()
			
	#} END query interface
	
	
class ObjectDBW(object):
	"""Defines an interface to create objects in the database"""
	
	def __init__(self, *args, **kwargs):
		self._ostream = None
	
	#{ Edit Interface
	def set_ostream(self, stream):
		"""
		Adjusts the stream to which all data should be sent when storing new objects
		
		:param stream: if not None, the stream to use, if None the default stream
			will be used.
		:return: previously installed stream, or None if there was no override
		:raise TypeError: if the stream doesn't have the supported functionality"""
		cstream = self._ostream
		self._ostream = stream
		return cstream
		
	def ostream(self):
		"""
		:return: overridden output stream this instance will write to, or None
			if it will write to the default stream"""
		return self._ostream
	
	def store(self, istream):
		"""
		Create a new object in the database
		:return: the input istream object with its sha set to its corresponding value
		
		:param istream: IStream compatible instance. If its sha is already set 
			to a value, the object will just be stored in the our database format, 
			in which case the input stream is expected to be in object format ( header + contents ).
		:raise IOError: if data could not be written"""
		raise NotImplementedError("To be implemented in subclass")
	
	def store_async(self, reader):
		"""
		Create multiple new objects in the database asynchronously. The method will 
		return right away, returning an output channel which receives the results as 
		they are computed.
		
		:return: Channel yielding your IStream which served as input, in any order.
			The IStreams sha will be set to the sha it received during the process, 
			or its error attribute will be set to the exception informing about the error.
			
		:param reader: async.Reader yielding IStream instances.
			The same instances will be used in the output channel as were received
			in by the Reader.
		
		:note:As some ODB implementations implement this operation atomic, they might 
			abort the whole operation if one item could not be processed. Hence check how 
			many items have actually been produced."""
		# base implementation uses store to perform the work
		task = ChannelThreadTask(reader, str(self.store_async), self.store) 
		return pool.add_task(task)
	
	#} END edit interface
	

class FileDBBase(object):
	"""Provides basic facilities to retrieve files of interest, including 
	caching facilities to help mapping hexsha's to objects"""
	
	def __init__(self, root_path):
		"""Initialize this instance to look for its files at the given root path
		All subsequent operations will be relative to this path
		:raise InvalidDBRoot: 
		:note: The base will not perform any accessablity checking as the base
			might not yet be accessible, but become accessible before the first 
			access."""
		super(FileDBBase, self).__init__()
		self._root_path = root_path
		
		
	#{ Interface 
	def root_path(self):
		""":return: path at which this db operates"""
		return self._root_path
	
	def db_path(self, rela_path):
		"""
		:return: the given relative path relative to our database root, allowing 
			to pontentially access datafiles"""
		return join(self._root_path, rela_path)
	#} END interface
		

class CachingDB(object):
	"""A database which uses caches to speed-up access"""
	
	#{ Interface 
	def update_cache(self, force=False):
		"""
		Call this method if the underlying data changed to trigger an update
		of the internal caching structures.
		
		:param force: if True, the update must be performed. Otherwise the implementation
			may decide not to perform an update if it thinks nothing has changed.
		:return: True if an update was performed as something change indeed"""
		
	# END interface


class CompoundDB(ObjectDBR, LazyMixin, CachingDB):
	"""A database which delegates calls to sub-databases.
	
	Databases are stored in the lazy-loaded _dbs attribute.
	Define _set_cache_ to update it with your databases"""
	def _set_cache_(self, attr):
		if attr == '_dbs':
			self._dbs = list()
		elif attr == '_db_cache':
			self._db_cache = dict()
		else:
			super(CompoundDB, self)._set_cache_(attr)
	
	def _db_query(self, sha):
		""":return: database containing the given 20 byte sha
		:raise BadObject:"""
		# most databases use binary representations, prevent converting 
		# it everytime a database is being queried
		try:
			return self._db_cache[sha]
		except KeyError:
			pass
		# END first level cache
		
		for db in self._dbs:
			if db.has_object(sha):
				self._db_cache[sha] = db
				return db
		# END for each database
		raise BadObject(sha)
	
	#{ ObjectDBR interface 
	
	def has_object(self, sha):
		try:
			self._db_query(sha)
			return True
		except BadObject:
			return False
		# END handle exceptions
		
	def info(self, sha):
		return self._db_query(sha).info(sha)
		
	def stream(self, sha):
		return self._db_query(sha).stream(sha)

	def size(self):
		""":return: total size of all contained databases"""
		return reduce(lambda x,y: x+y, (db.size() for db in self._dbs), 0)
		
	def sha_iter(self):
		return chain(*(db.sha_iter() for db in self._dbs))
		
	#} END object DBR Interface
	
	#{ Interface
	
	def databases(self):
		""":return: tuple of database instances we use for lookups"""
		return tuple(self._dbs)

	def update_cache(self, force=False):
		# something might have changed, clear everything
		self._db_cache.clear()
		stat = False
		for db in self._dbs:
			if isinstance(db, CachingDB):
				stat |= db.update_cache(force)
			# END if is caching db
		# END for each database to update
		return stat
	#} END interface
		

