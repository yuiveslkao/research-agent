# Dependency-free JSON writer (strings, numbers, bools, nothing, Dict, Vector).
# High-precision numbers must be passed as decimal strings, never as Float64.
module MiniJSON
export write_json

function esc(s::AbstractString)
    io = IOBuffer()
    for c in s
        if c == '"';      print(io, "\\\"")
        elseif c == '\\'; print(io, "\\\\")
        elseif c == '\n'; print(io, "\\n")
        elseif c == '\t'; print(io, "\\t")
        elseif c < ' ';   print(io, "\\u", lpad(string(UInt16(c), base = 16), 4, '0'))
        else print(io, c)
        end
    end
    String(take!(io))
end

emit(io, x::AbstractString) = print(io, '"', esc(x), '"')
emit(io, x::Bool) = print(io, x ? "true" : "false")
emit(io, x::Integer) = print(io, x)
emit(io, ::Nothing) = print(io, "null")
function emit(io, x::AbstractDict)
    print(io, '{')
    for (i, (k, v)) in enumerate(sort!(collect(x); by = kv -> string(kv[1])))
        i > 1 && print(io, ',')
        emit(io, string(k)); print(io, ':'); emit(io, v)
    end
    print(io, '}')
end
function emit(io, x::AbstractVector)
    print(io, '[')
    for (i, v) in enumerate(x)
        i > 1 && print(io, ',')
        emit(io, v)
    end
    print(io, ']')
end
emit(io, x::Real) = error("pass reals as decimal strings (got $(typeof(x)))")

write_json(path::AbstractString, x) = open(io -> (emit(io, x); println(io)), path, "w")

end # module
